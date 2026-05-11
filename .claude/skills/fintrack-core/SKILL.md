[SKILL.md](https://github.com/user-attachments/files/27562107/SKILL.md)
---
name: fintrack-core
description: >
  Core context for Kirk's personal finance tracking system (Fintrack CL).
  Use this skill in any Claude Code session or Routine that needs to read,
  insert, query, or analyze financial transactions in the Supabase project
  `japiqczoxgxnygwakmcm`. Always load this skill before any Fintrack-related
  SQL, insert, or analysis task. Routes to references/ for specific workflows
  (Gmail daily sync, email digest, statement parsing, etc.).
---

# 🏦 Fintrack CL — Core (repo-side)

**Supabase project:** `japiqczoxgxnygwakmcm`
**Dashboard:** `fintrackcl.netlify.app`
**Stack:** Supabase (DB + Edge Functions) · Render (Telegram bot) · Netlify (React) · Claude/Gemini APIs

---

## 🧠 Interaction rules — always on

- **BLUF first** — totals/answer first, detail second
- **Never insert without confirmation** in interactive sessions (Routines auto-insert is OK if the prompt explicitly says so)
- **Never delete** without explicit "sí, bórralo"
- **One step at a time** — finish one, confirm, move on
- **Show max 5 items** before "¿quieres ver más?"
- **Show full errors** — never swallow them
- **ADHD-friendly:** scannable bullets, emojis, bold key terms, Spanglish OK

---

## ⚠️ Universal parsing rules

| Issue | Rule |
|---|---|
| Chilean amount format | `$118.137` = **118137 CLP**. Dot is thousand separator, NOT decimal. Always strip `$`/`CLP`/spaces, remove `.`, parse as int. |
| Comma-decimal (rare) | `$1.234,56` = 1234.56 CLP. Only relevant for non-CLP currencies. |
| Sign convention | **Expenses → NEGATIVE** (e.g., `-12230`). Income / transfers-in → **POSITIVE**. |
| `amount = 0` | Blocked by `chk_nonzero_amount` constraint. Sanitize before insert. |
| Duplicates | Fuzzy match on `amount + date(±1d) + description_clean`. If found → enrich existing record with email data (see `references/gmail-sync.md` Step 5). Only skip if nothing new to add. |
| Unknown account | Set `account_id = NULL`. Don't invent. |
| `is_mine` filter | In analysis queries, always `WHERE accounts.is_mine = true`. |

---

## 🗄️ `transactions` table schema (real fields)

```
date                 DATE        — YYYY-MM-DD
description_raw      TEXT        — original from email/statement
description_clean    TEXT        — normalized merchant name (e.g., "Uber" not "UBER*TRIP HELP.UBER.CO")
amount               INT         — CLP, signed (negative=expense)
currency             TEXT        — default 'CLP'
category_slug        TEXT        — see slugs below (NEVER Spanish names)
account_id           UUID        — see account map below; NULL if unknown
transaction_type     TEXT        — expense | transfer_out | transfer_in | income | payment | fees
notes                TEXT        — extra context (cuota number, location, "source: gmail_routine", etc.)
counterpart_name     TEXT        — for transfers; person/company on the other end
counterpart_rut      TEXT        — RUT if available
counterpart_bank     TEXT        — bank of counterpart for transfers
bank_reference       TEXT        — operation/comprobante number
```

---

## 🏷️ Category slugs (USE THESE EXACT VALUES)

```
food, transport, shopping, health, entertainment, fitness,
personal_care, utilities, housing, fees, loan_payment, payment,
subscriptions, travel, education, donations, pets, other
```

If unsure → `other`. Never invent slugs. Never use Spanish names.

---

## 🆔 Account UUIDs

### Credit cards / general (`ACCOUNT_MAP`)
| Bank | UUID |
|---|---|
| Itaú | `0e1f70bf-013a-416d-b6fb-bc55ed30b9f1` |
| Scotiabank | `f5b382e4-4625-4b70-a890-ca44ace192fd` |
| Banco de Chile | `9636953b-d4cb-4a4a-b193-76796cc9c51d` |
| Tenpo | `f30ac63e-b0e2-4b5f-ad62-fd7a7671ff0b` |
| CMR Falabella | `b3428f73-ae68-48ce-820c-3dcae69d9873` |

### Cuenta corriente / vista (`CTA_ACCOUNT_MAP`)
| Bank | UUID |
|---|---|
| Itaú | `7270f43c-a0f8-49b7-bef4-2371f20aa75c` |
| Tenpo | `c01d4b83-201d-4f49-bdf4-ca9e28d1ae9f` |
| Mercado Pago | `f0b03168-2fa2-4edc-8779-c4ab1d17d350` |
| Banco de Chile | `e7627b0a-679d-4b3e-b1b6-70b819c9c469` |
| Scotiabank | `3b793c1f-906b-4469-a52c-3a6e7f960285` |

**Lookup logic:** Try matching email sender / body text against bank names (lowercase substring). For TC purchases use `ACCOUNT_MAP`. For transfers/abonos use `CTA_ACCOUNT_MAP`. If no match → `account_id = NULL`.

---

## 🏦 `transaction_type` decision tree

| Signal | Type |
|---|---|
| Compra con tarjeta de crédito/débito, pago de servicio, comisión | `expense` (NEGATIVE) |
| Transferencia enviada a tercero ("TRF A", "TRANSF A", nombre destinatario) | `transfer_out` (NEGATIVE) |
| Abono recibido ("ABONO", "TRANSFERENCIA DE", "TRANSF DE") | `transfer_in` (POSITIVE) |
| Sueldo, honorarios, pensión, devolución impuestos | `income` (POSITIVE) |
| Pago a tu propia TC ("PAGO TC", "PAGO TARJETA") | `payment` (NEGATIVE for the source account) |

---

## 📚 References — load only what the task needs

| Task | Load → |
|---|---|
| Daily Gmail sync routine (9 AM) | [`references/gmail-sync.md`](references/gmail-sync.md) |
| Daily email digest routine (8:30 AM) | [`references/email-digest.md`](references/email-digest.md) |
| Categorize a merchant you haven't seen | [`references/merchant-rules.md`](references/merchant-rules.md) *(create as needed)* |

---

## 🚦 Default flow

1. Identify task type (insert / query / analysis / digest)
2. Load matching reference file
3. For INSERT: build summary first → confirm → execute (or auto-execute in routines if the routine prompt explicitly authorizes)
4. For SELECT: apply `is_mine` filter → run → present BLUF-first
5. End every routine with the Telegram push step (see references)
