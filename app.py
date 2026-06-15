import streamlit as st
import requests
import base64
import json
import io
import csv
from datetime import datetime
from PIL import Image
import gspread
from google.oauth2.service_account import Credentials

# ── Config ────────────────────────────────────────────────────────────────────
OLLAMA_HOST = st.secrets.get("OLLAMA_HOST", "https://ollama.com")
OLLAMA_CLOUD_URL = OLLAMA_HOST + "/api/chat"
MODEL = st.secrets.get("OLLAMA_MODEL", "qwen3-vl:235b-cloud")

SHEET_NAME = st.secrets.get("GOOGLE_SHEET_NAME", "Invoice Parser")
WORKSHEET_INVOICES = "Invoices"
WORKSHEET_LINE_ITEMS = "Line Items"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

SYSTEM_PROMPT = """You are an invoice data extraction assistant. Given an image of an invoice or bill, extract structured data and return ONLY a valid JSON object with no markdown, no explanation, no code fences. Use null for missing fields.

Return this exact schema:
{
  "vendor_name": "string or null",
  "invoice_number": "string or null",
  "invoice_date": "string or null",
  "due_date": "string or null",
  "bill_to": "string or null",
  "subtotal": "string or null",
  "tax": "string or null",
  "total": "string or null",
  "payment_terms": "string or null",
  "currency": "string or null",
  "line_items": [
    { "description": "string", "quantity": "string or null", "unit_price": "string or null", "amount": "string or null" }
  ]
}"""

FIELD_LABELS = {
    "vendor_name": "Vendor",
    "invoice_number": "Invoice #",
    "invoice_date": "Invoice Date",
    "due_date": "Due Date",
    "bill_to": "Bill To",
    "subtotal": "Subtotal",
    "tax": "Tax",
    "total": "Total",
    "payment_terms": "Payment Terms",
    "currency": "Currency",
}

TOP_FIELDS = list(FIELD_LABELS.keys())
INVOICE_HEADER = ["Extracted At", "File"] + list(FIELD_LABELS.values())
LINE_ITEM_HEADER = ["Extracted At", "File", "Invoice #", "Line #", "Description", "Qty", "Unit Price", "Amount"]

# ── Google Sheets ─────────────────────────────────────────────────────────────

@st.cache_resource
def get_gspread_client():
    creds_dict = st.secrets["gcp_service_account"]
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    return gspread.authorize(creds)


def get_or_create_worksheets(gc):
    """Open the spreadsheet and ensure both worksheets exist with headers."""
    try:
        sh = gc.open(SHEET_NAME)
    except gspread.SpreadsheetNotFound:
        sh = gc.create(SHEET_NAME)
        # Share with the service account owner so it's accessible
        sh.share(None, perm_type="anyone", role="writer")

    ws_names = [ws.title for ws in sh.worksheets()]

    if WORKSHEET_INVOICES not in ws_names:
        ws_inv = sh.add_worksheet(title=WORKSHEET_INVOICES, rows=1000, cols=len(INVOICE_HEADER))
        ws_inv.append_row(INVOICE_HEADER, value_input_option="RAW")
    else:
        ws_inv = sh.worksheet(WORKSHEET_INVOICES)

    if WORKSHEET_LINE_ITEMS not in ws_names:
        ws_li = sh.add_worksheet(title=WORKSHEET_LINE_ITEMS, rows=2000, cols=len(LINE_ITEM_HEADER))
        ws_li.append_row(LINE_ITEM_HEADER, value_input_option="RAW")
    else:
        ws_li = sh.worksheet(WORKSHEET_LINE_ITEMS)

    return sh, ws_inv, ws_li


def append_to_sheets(gc, filename: str, data: dict, extracted_at: str):
    """Append one invoice (header row + line item rows) to Google Sheets."""
    _, ws_inv, ws_li = get_or_create_worksheets(gc)

    # Invoices sheet — one row per invoice
    inv_row = [extracted_at, filename] + [data.get(k) or "" for k in TOP_FIELDS]
    ws_inv.append_row(inv_row, value_input_option="USER_ENTERED")

    # Line items sheet — one row per line item
    items = data.get("line_items") or []
    invoice_number = data.get("invoice_number") or ""
    li_rows = [
        [
            extracted_at,
            filename,
            invoice_number,
            i + 1,
            item.get("description") or "",
            item.get("quantity") or "",
            item.get("unit_price") or "",
            item.get("amount") or "",
        ]
        for i, item in enumerate(items)
    ]
    if li_rows:
        ws_li.append_rows(li_rows, value_input_option="USER_ENTERED")

    return f"https://docs.google.com/spreadsheets/d/{_.id}"


# ── Extraction helpers ────────────────────────────────────────────────────────

def image_to_base64(uploaded_file) -> str:
    img = Image.open(uploaded_file)
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def extract_invoice(b64: str, api_key: str | None) -> dict:
    headers = {"Content-Type": "application/json"}
    key = api_key or st.secrets.get("OLLAMA_API_KEY")
    if key:
        headers["Authorization"] = f"Bearer {key}"

    payload = {
        "model": MODEL,
        "stream": False,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "image", "data": b64},
                    {"type": "text", "text": "Extract all invoice fields from this image."},
                ],
            },
        ],
    }

    resp = requests.post(OLLAMA_CLOUD_URL, headers=headers, json=payload, timeout=60)
    resp.raise_for_status()

    data = resp.json()
    raw = data["message"]["content"]
    clean = raw.replace("```json", "").replace("```", "").strip()
    return json.loads(clean)


def results_to_csv(results: list[dict]) -> str:
    header = ["File"] + list(FIELD_LABELS.values()) + ["Line #", "Description", "Qty", "Unit Price", "Amount"]
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)

    for r in results:
        fname = r["filename"]
        data = r["data"]
        base = [fname] + [data.get(k) or "" for k in TOP_FIELDS]
        items = data.get("line_items") or []

        if not items:
            writer.writerow(base + ["", "", "", "", ""])
        else:
            for i, item in enumerate(items):
                prefix = base if i == 0 else [""] * len(base)
                writer.writerow(prefix + [
                    i + 1,
                    item.get("description") or "",
                    item.get("quantity") or "",
                    item.get("unit_price") or "",
                    item.get("amount") or "",
                ])

    return buf.getvalue()


# ── UI ────────────────────────────────────────────────────────────────────────

st.set_page_config(page_title="Invoice Parser", page_icon="📄", layout="wide")

st.markdown("""
<style>
    .block-container { padding-top: 2rem; max-width: 900px; }
    .stDataFrame { border-radius: 8px; }
    div[data-testid="metric-container"] {
        background: #1a1d27;
        border: 1px solid #252838;
        border-radius: 8px;
        padding: 12px 16px;
    }
</style>
""", unsafe_allow_html=True)

st.title("📄 Invoice Parser")
st.caption(f"Powered by Ollama Cloud · `{MODEL}`")

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("Configuration")
    api_key = st.text_input("Ollama Cloud API Key", type="password", placeholder="sk-... (optional)")
    st.divider()

    # Google Sheets status
    sheets_enabled = "gcp_service_account" in st.secrets
    if sheets_enabled:
        st.success("✓ Google Sheets connected")
        sheet_url = st.session_state.get("sheet_url")
        if sheet_url:
            st.markdown(f"[Open sheet ↗]({sheet_url})")
    else:
        st.warning("Google Sheets not configured.\nAdd `gcp_service_account` to secrets to enable.")

    st.divider()
    st.markdown("**Extracted fields**")
    for label in FIELD_LABELS.values():
        st.markdown(f"- {label}")
    st.markdown("- Line items (description, qty, unit price, amount)")

# ── Session state ─────────────────────────────────────────────────────────────
if "results" not in st.session_state:
    st.session_state.results = []
if "sheet_url" not in st.session_state:
    st.session_state.sheet_url = None

# ── Upload ────────────────────────────────────────────────────────────────────
uploaded = st.file_uploader(
    "Upload invoice images",
    type=["jpg", "jpeg", "png", "webp"],
    accept_multiple_files=True,
)

if uploaded:
    already_done = {r["filename"] for r in st.session_state.results}
    new_files = [f for f in uploaded if f.name not in already_done]

    if new_files:
        if st.button(
            f"Extract {len(new_files)} invoice{'s' if len(new_files) != 1 else ''}",
            type="primary",
            use_container_width=True,
        ):
            gc = get_gspread_client() if sheets_enabled else None
            progress = st.progress(0, text="Starting…")
            extracted_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

            for i, f in enumerate(new_files):
                progress.progress(i / len(new_files), text=f"Processing {f.name}…")
                try:
                    b64 = image_to_base64(f)
                    data = extract_invoice(b64, api_key or None)
                    st.session_state.results.append({"filename": f.name, "data": data})

                    if gc:
                        url = append_to_sheets(gc, f.name, data, extracted_at)
                        st.session_state.sheet_url = url

                except Exception as e:
                    st.error(f"**{f.name}**: {e}")

            progress.progress(1.0, text="Done!")
    else:
        st.info("All uploaded files have already been processed. Upload new files to extract more.")

# ── Results ───────────────────────────────────────────────────────────────────
if st.session_state.results:
    st.divider()

    col1, col2, col3, col4 = st.columns([2, 1, 1, 1])
    with col1:
        st.subheader(f"Results — {len(st.session_state.results)} invoice{'s' if len(st.session_state.results) != 1 else ''}")
    with col2:
        json_bytes = json.dumps(
            [{"file": r["filename"], **r["data"]} for r in st.session_state.results],
            indent=2,
        ).encode()
        st.download_button("↓ JSON", json_bytes, "invoices.json", "application/json", use_container_width=True)
    with col3:
        csv_str = results_to_csv(st.session_state.results)
        st.download_button("↓ CSV", csv_str, "invoices.csv", "text/csv", use_container_width=True)
    with col4:
        if st.session_state.sheet_url:
            st.link_button("↗ Sheet", st.session_state.sheet_url, use_container_width=True)

    for r in st.session_state.results:
        with st.expander(f"📄 {r['filename']}", expanded=True):
            data = r["data"]

            cols = st.columns(5)
            for col, (key, label) in zip(cols, [
                ("vendor_name", "Vendor"), ("invoice_number", "Invoice #"),
                ("invoice_date", "Date"), ("due_date", "Due Date"), ("total", "Total"),
            ]):
                col.metric(label, data.get(key) or "—")

            sec_cols = st.columns(5)
            for col, (key, label) in zip(sec_cols, [
                ("bill_to", "Bill To"), ("subtotal", "Subtotal"),
                ("tax", "Tax"), ("payment_terms", "Payment Terms"), ("currency", "Currency"),
            ]):
                col.metric(label, data.get(key) or "—")

            items = data.get("line_items") or []
            if items:
                st.markdown("**Line Items**")
                st.dataframe(
                    items,
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "description": st.column_config.TextColumn("Description"),
                        "quantity": st.column_config.TextColumn("Qty"),
                        "unit_price": st.column_config.TextColumn("Unit Price"),
                        "amount": st.column_config.TextColumn("Amount"),
                    },
                )

    if st.button("Clear all results", use_container_width=True):
        st.session_state.results = []
        st.rerun()
