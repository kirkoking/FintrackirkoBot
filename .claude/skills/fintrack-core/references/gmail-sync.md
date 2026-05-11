# 📧 Gmail Sync Routine — Daily 9 AM

This reference defines the workflow for the **Fintrack Gmail Sync** routine that runs every morning at 9:00 AM (Chile time) and inserts transactions detected in Kirk's Gmail into Supabase.

> **Always load `../SKILL.md` first** for parsing rules, schema, account UUIDs, and categories.

---

## Step 1 — Search Gmail (last 24h)

Use the Gmail connector with these queries:

1. `from:enviodigital@bancochile.cl OR from:avisos.info@scotiabank.cl newer_than:1d`
2. `(transferencia OR comprobante OR pago) (bancochile OR scotiabank OR tenpo OR mercadopago OR falabella OR bci OR itau) newer_than:1d`
3. `from:tenpo.cl OR from:mercadopago.cl OR from:itau.cl newer_than:1d`
4. `from:comunidadfeliz.com OR from:chile.enel.com OR from:cruzblanca.cl OR from:metgas.cl newer_than:1d`
5. `("tarjeta de crédito" OR "compra realizada" OR "cuota") (bancochile OR scotiabank OR falabella OR tenpo OR bci OR itau) newer_than:1d`
6. `(boleta OR factura OR "estado de cuenta") has:attachment newer_than:1d`

Dedupe matches across queries by message ID before processing.

---

## Step 2 — Read each email + attachments

For each unique email:

1. **Read the body** with the Gmail connector
2. **If it has a PDF attachment** (estado de cuenta, boleta SII, factura):
   - Download the attachment
   - Use `pdftotext` (shell) or `pypdf` (Python) to extract text
   - Parse the extracted text — line-item detail is often only in the PDF
3. **Skip these signals** (NOT transactions):
   - "Cashback recibido" → that's a marketing notice, not a tx (unless explicitly "abono cashback $X" in the cartola)
   - "Tu compra fue rechazada" → no tx happened
   - Marketing / promotional emails
   - "Saldo disponible:" lines (informational)
   - Authentication codes / OTPs

---

## Step 3 — Extract structured data

For each transaction, build a row matching the schema in `SKILL.md`:

```json
{
  "date": "YYYY-MM-DD",
  "description_raw": "Original email subject or merchant string",
  "description_clean": "Normalized merchant (e.g. 'Uber' not 'UBER*TRIP')",
  "amount": -12230,
  "currency": "CLP",
  "category_slug": "transport",
  "account_id": "<uuid from ACCOUNT_MAP / CTA_ACCOUNT_MAP, or null>",
  "transaction_type": "expense",
  "notes": "source: gmail_routine | subject: <email subject> | <extra context>",
  "counterpart_name": null,
  "counterpart_rut": null,
  "counterpart_bank": null,
  "bank_reference": null
}
```

**Account resolution:**
- TC purchase email mentions BdCh / Scotia / Itaú / Tenpo / CMR / Falabella → use `ACCOUNT_MAP`
- Transfer/abono notification on cuenta corriente → use `CTA_ACCOUNT_MAP`
- No clear bank → `account_id = null`

**Counterparts (transfers only):**
- For `transfer_out` / `transfer_in`, fill `counterpart_name` (and `counterpart_bank` if visible)
- For `bank_reference`, capture the operation/comprobante number

---

## Step 4 — Present summary BEFORE inserting

Build a Markdown summary table:

```
### 📊 Fintrack Daily — [YYYY-MM-DD]

**Total movimientos:** X
**Gasto total:** $XXX.XXX CLP
**Ingresos:** $XXX.XXX CLP

| Hora  | Monto      | Comercio        | Cuenta     | Categoría | Tipo      |
|-------|------------|-----------------|------------|-----------|-----------|
| 14:32 | -$12.230   | Uber            | BdCh ****6875 | transport | expense   |
| ...   |            |                 |            |           |           |

**Por categoría:**
- transport: -$XX.XXX (X tx)
- food: -$XX.XXX (X tx)
...
```

If 0 transactions: "✅ Sin movimientos detectados en las últimas 24h" → skip steps 5-6 → still send Telegram push with that message.

---

## Step 5 — Upsert into Supabase (insert or enrich)

The email is **more reliable than OCR** for structured fields (merchant name, amount digits, account, bank reference). If the same transaction was already uploaded as a photo via the bot, **enrich and correct it** rather than skipping it.

### 5a — Fuzzy match lookup

Query `transactions` for a potential existing record:

```sql
SELECT id, description_clean, description_raw, account_id,
       counterpart_name, counterpart_bank, bank_reference,
       category_slug, notes, date, amount
FROM transactions
WHERE amount = <tx amount>
  AND date BETWEEN <tx date - 1 day> AND <tx date + 1 day>
  AND description_clean ILIKE '%<first meaningful word of tx description_clean>%'
LIMIT 1;
```

- Allow ±1 day on date because boleta date (purchase) may differ from bank notification date (processing).
- If no match: **insert** (go to 5c).
- If match found: **enrich** (go to 5b).

### 5b — Enrich existing record (match found)

Build an `UPDATE` payload with these rules per field:

| Field | Rule |
|---|---|
| `description_clean` | **Email wins.** Replace with email's value — OCR misreads merchant names. |
| `description_raw` | **Append only.** Set to `<original> \| email: <email's description_raw>` so both sources are preserved. Skip if email's raw is already contained. |
| `account_id` | **Email wins if existing is NULL.** Email knows which card/account; don't overwrite a manually-set value. |
| `counterpart_name` | **Email wins if existing is NULL.** |
| `counterpart_bank` | **Email wins if existing is NULL.** |
| `bank_reference` | **Email wins if existing is NULL.** |
| `transaction_type` | **Email wins if existing is NULL** or was inferred as `"expense"` generically. |
| `category_slug` | **Never overwrite.** User may have manually corrected it. |
| `date` | **Never overwrite.** Keep the boleta/original date. |
| `amount` | **Never overwrite.** If amounts differ by >2%, log a warning in the sync status but don't change the record. |
| `notes` | **Append only.** Add `\| gmail_enriched \| subject: <email subject>` to existing notes. Never remove prior notes. |

Only include fields that actually changed in the UPDATE (skip no-op updates).

After updating, count as **"enriched"** (not skipped, not inserted).

### 5c — Insert new record

If no fuzzy match was found, insert the full row:

```sql
INSERT INTO transactions (...) VALUES (...) ON CONFLICT DO NOTHING;
```

### 5d — Track counts

- **inserted:** new records added
- **enriched:** existing records updated with email data
- **skipped:** true duplicates where email added nothing new (all fields already matched)
- **flagged:** amount mismatch >2% between email and existing record (log both amounts)
- **failed:** insert/update errors (log error message)

---

## Step 6 — Telegram push

End the routine by sending the summary to Telegram. Use the routine's environment variables:
- `TELEGRAM_BOT_TOKEN` (same token the bot uses)
- `TELEGRAM_CHAT_ID` (Kirk's chat ID)

Run a shell command:

```bash
curl -s -X POST \
  "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
  -H "Content-Type: application/json" \
  -d "{
    \"chat_id\": \"${TELEGRAM_CHAT_ID}\",
    \"text\": \"<the summary, Markdown-formatted>\",
    \"parse_mode\": \"Markdown\"
  }"
```

If the message exceeds 4096 chars (Telegram limit), split into chunks at category boundaries.

---

## Step 7 — Final status block

Append to the routine output (visible in run logs at claude.ai/code/routines):

```
### 🗄️ Sync Status
- ✅ X inserted
- 🔄 X enriched (existing records corrected/completed with email data)
- ⏭️ X skipped (true duplicates, nothing new to add)
- ⚠️ X flagged (amount mismatch >2% — review manually)
  - <date> | <description_clean> | existing: $X | email: $Y
- ❌ X failed
  - <error message 1>
- 📲 Telegram push: ✅ sent / ❌ failed
```

---

## Edge cases / gotchas

- **Banco de Chile credit card emails:** sometimes amount appears in body as "Por un monto de $X.XXX" — the dot-thousand-separator rule applies.
- **Cuotas (installments):** Email like "Cuota 3/12 - Falabella". Use `transaction_type: "expense"`, add `notes: "source: gmail_routine | subject: <email subject> | cuota 3/12"`.
- **Tenpo cashback:** if literally "Recibiste $X de cashback" with no actual abono, **skip**. If it's an "Abono cashback" line in a cartola, count as `transfer_in`.
- **Refunds / reversiones:** use `transaction_type: "transfer_in"` with positive amount; mention "reversión" in notes.
- **MercadoPago notifications:** often duplicate (one for the buyer, one for the wallet). Dedupe carefully on amount + description.
- **Email is in another currency (USD, EUR):** preserve `currency` field, do NOT auto-convert to CLP.
