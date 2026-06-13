# 🏦 Cartola (bank statement) parsing — bank-by-bank quirks

> Learnings captured while ingesting real cartolas. **Load this before parsing any cartola PDF.** Grows as we process more banks.

## 🔑 Golden rule: read the COLUMN, not the verb

Chilean cartolas have separate **CARGOS (out)** and **ABONOS (in)** columns. The word a line starts with does NOT reliably tell you the direction — the column does. After parsing, **reconcile your totals against the cartola's printed `TOTAL CARGOS` and `TOTAL ABONOS`** before inserting anything. If they don't match, you misread a sign.

## Encrypted PDFs

Most bank cartolas arrive password-protected. Passwords are derived from Kirk's RUT `23185611-0`:

| Bank | Password |
|---|---|
| Banco de Chile | last 4 digits of RUT w/o verifier → `5611` |
| Scotiabank | last 4 digits before the dash → `5611` |
| Itaú | full RUT w/o dots or verifier → `23185611` |
| Tenpo | RUT w/o verifier → `23185611` |

Decrypt with `pikepdf` (does NOT need the `cryptography` lib), then extract text with `pypdf`/`pdfplumber`. (`pip install cffi` if the system `cryptography` rust binding is broken.)

## Banco de Chile — cuenta corriente

Line prefixes and their direction:

| Prefix in cartola | Direction | type / sign |
|---|---|---|
| `TRASPASO A:<nombre>` | OUT | `transfer_out` (NEGATIVE) |
| `CARGO POR PAGO TC` | OUT | `transfer_out` cat `payment` (NEGATIVE) — paying own credit card |
| `CARGO SEGURO …` | OUT | `expense` (NEGATIVE) |
| `AMORTIZACION A LINEA DE CREDITO` | OUT | credit-line repayment (NEGATIVE) — internal plumbing |
| `INTERESES / IMPUESTO LINEA DE CREDITO` | OUT | `expense` cat `fees` (NEGATIVE) |
| `SRV CPRA USD …` | OUT | `expense` cat `fees` (NEGATIVE) |
| **`PAGO:PROVEEDORES <convenio>`** | **IN** ⚠️ | **ABONO (POSITIVE)** — reembolso/subsidio via convenio, NOT an outflow |
| `PAGO:DEV IMPUESTO …` | IN | `income` (POSITIVE) — tax refund |
| `TRASPASO DE:<nombre>` | IN | `transfer_in` (POSITIVE) |
| `TRANSFERENCIA DESDE LINEA DE CREDITO` | IN | credit-line draw (POSITIVE) — internal plumbing |
| `SALDO INICIAL / SALDO FINAL` | — | running balance, NOT a transaction — skip |

**Known convenios (biller IDs) seen on Kirk's account** — all are INFLOWS (abonos):
- `0762966190` → **Colmena** reembolsos médicos. Cross-check vs email "Reembolso Web COLMENA" (`noresponder@colmena.cl`); the cartola line is the *deposit landing* of an already-approved reembolso → **associate/merge, don't double-count**.
- `0965014500` → **licencia médica** subsidio (Compin), e.g. $1.505.419 = 2-week leave. **Income** (replacement pay), NOT a medical expense.
- `0765074436` → **Cruz Blanca** reembolsos médicos ambulatorios (varied, incl. large like $566.464). Cross-check vs "Solicitud/Autorización de Reembolso Ambulatorio" from `CanalWebSolicReem@cruzblanca.cl` / `informativo.cruzblanca@cruzblanca.cl`. `transfer_in`.

### 🔎 Context lookup — when a movement is unclear, SEARCH GMAIL before guessing
Before classifying an unknown convenio / counterparty / big amount, **search Gmail** for the **amount** (Chilean format `123.456`), the **counterparty name**, or the **date** to find the matching notice (reembolso, transferencia, boleta). Senders worth checking: `noresponder@colmena.cl`, `*@cruzblanca.cl`, `serviciodetransferencias@bancochile.cl`, `pagos@encuadrado.com` (teleconsultas médicas). Only ask Kirk if Gmail turns up nothing. *(Kirk's request — let the system self-serve context instead of asking.)*

**Línea de crédito plumbing:** `TRANSFERENCIA DESDE` (draw) + `AMORTIZACION A` (repay) net out and are not real income/expense. Default: record but keep OUT of spending analysis (or skip), per Kirk's call.

**Salary note:** Kirk's Decathlon sueldo does NOT land in this BdCh cuenta corriente — look elsewhere for the monthly remuneración deposit.

## Scotiabank

- `CartolaCliente.pdf` (small, ~10KB) = **cuenta corriente** (often near-empty / dormant). The active Scotia account is the **tarjeta de crédito** (`Estado-de-Cuenta-Scotiabank-*.pdf`) — make sure you have the TC statement, not just the CC.

## Open TODO
- Map Tenpo, Itaú, CMR Falabella, Mercado Pago line prefixes as we process them.
- Build these rules into the bot/importer parser so it stops guessing signs (and asks Kirk when a convenio is unknown).
