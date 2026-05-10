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

## Step 5 — Insert into Supabase

Use the Supabase connector. For each transaction:

1. **Dedupe check:** Query `transactions` table for any row where:
   - `date = <tx date>` AND
   - `amount = <tx amount>` AND
   - `description_clean ILIKE <tx description_clean>`

   If a match exists → skip silently, count as "skipped"
2. **Insert** the row using `INSERT ... ON CONFLICT DO NOTHING`
3. Track: inserted count, skipped count, failed count (with error messages)

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
- ⏭️ X skipped (duplicates)
- ❌ X failed
  - <error message 1>
  - <error message 2>
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
