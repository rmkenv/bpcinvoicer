# Invoice Parser

Streamlit app that extracts structured data from invoice images using Ollama Cloud (`gpt-oss:20b`) and appends results to Google Sheets.

## Features

- Upload JPG/PNG invoice images (batch supported)
- Extracts: vendor, invoice #, dates, totals, line items
- Preview all fields inline before downloading
- Export as JSON or CSV
- Auto-appends to Google Sheets (two tabs: Invoices + Line Items)

## Google Sheets setup

1. Go to [Google Cloud Console](https://console.cloud.google.com)
2. Create a project → enable **Google Sheets API** and **Google Drive API**
3. Create a **Service Account** → generate a JSON key
4. Copy the JSON key fields into `.streamlit/secrets.toml` under `[gcp_service_account]`
5. The app will auto-create the spreadsheet named `Invoice Parser` on first run

## Local setup

```bash
pip install -r requirements.txt
streamlit run app.py
```

Fill in `.streamlit/secrets.toml` (gitignored):

```toml
OLLAMA_CLOUD_URL = "https://your-endpoint/v1/chat/completions"
GOOGLE_SHEET_NAME = "Invoice Parser"

[gcp_service_account]
type = "service_account"
project_id = "..."
private_key_id = "..."
private_key = "-----BEGIN RSA PRIVATE KEY-----\n...\n-----END RSA PRIVATE KEY-----\n"
client_email = "...@....iam.gserviceaccount.com"
client_id = "..."
auth_uri = "https://accounts.google.com/o/oauth2/auth"
token_uri = "https://oauth2.googleapis.com/token"
auth_provider_x509_cert_url = "https://www.googleapis.com/oauth2/v1/certs"
client_x509_cert_url = "..."
```

## Deploy to Streamlit Cloud

1. Push this repo to GitHub (secrets.toml is gitignored)
2. Go to [share.streamlit.io](https://share.streamlit.io) → New app → select repo → `app.py`
3. Under **Advanced settings → Secrets**, paste the full contents of your `secrets.toml`
4. Deploy

## Sheet structure

**Invoices tab** — one row per invoice:
`Extracted At | File | Vendor | Invoice # | Date | Due Date | Bill To | Subtotal | Tax | Total | Payment Terms | Currency`

**Line Items tab** — one row per line item:
`Extracted At | File | Invoice # | Line # | Description | Qty | Unit Price | Amount`
