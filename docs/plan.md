# Bindal Ops Agent — plan

## Status (20 Oct 2026)

Model: free Cloudflare Workers AI (`@cf/openai/gpt-oss-120b`) through the Telegram worker's `/api/ai`;
no paid API. All Telegram messages in English.

| # | Task | Status | Bot / when |
|---|---|---|---|
| D1 | Morning sale report | ✅ done | `morning` 9:30 AM |
| D2 | AJIO courier scorecard | ✅ done | inside `ajio-tat` 5:30 PM |
| D3 | SLA warning | ✅ done | `sla` 12 PM, 3 PM |
| D4 | ERP label vs OMSGuru | ✅ done | `labels` 6 PM |
| D5 | Inventory push + failure alert | ✅ done | `inv-push` every 3 h |
| D6 | Stock-out alert | ✅ done | `stock` 10 AM |
| D7 | Myntra VMS | ✅ already there | `vms` 5:30 PM |
| D8 | Bot watch (agent) | ✅ done | `agent-watch` after every run |
| D9 | Evening summary | ✅ done | `evening` 8 PM |
| W1 + W5 | Weekly review + packers | ✅ done | `weekly` Monday 10:15 AM |
| W2, W6, M3 | Returns / ads | ✅ already there | `returns`, `ajio-ads` (monthly) |
| W3 | SPF claims | ✅ already there | `ajio-spf`, `myntra-spf` daily reports |
| M4 | Dead / slow stock | ✅ done | `deadstock` the 1st, 11 AM |
| E1-E4 | Run failures, order lookup, `--orders`, push now | ✅ agent-watch / by hand | |
| E5 | Sudden drop alert | ✅ done | inside `morning` |
| W4 | Courier scorecard: Myntra | ✅ done | `myntra-tat` 6:30 PM (fmpu date vs OMSGuru SLA) |
| W4 | Courier scorecard: Flipkart | ⏳ no pickup-time source yet (Ekart gives status only) | |
| W7 | Listing health | ✅ done | `listings` 11:30 AM |
| M1 | Monthly MIS | ✅ done; net after commission once `MIS_RATES` is set | `mis` the 2nd, 11 AM |
| M5 | This month's plan | ✅ done (inside `mis`) | |
| §2 Hissa 2 | Telegram chat with the agent | ✅ done | plain text to the bot -> `agent-chat` |
| §2 Hissa 3 | Approvals (Approve / Reject buttons) | ✅ done | worker `/api/approval`, `AGENT_ACTIONS` |
| §2 Hissa 3 | Code-fix PRs by the agent | ❌ not with the free model (too weak to change code safely) | |
| W8 | Marketplace notices / penalties | ❌ needs each seller panel's notice page | |
| M2 | Payment reconciliation | ❌ needs each marketplace's settlement report | |


*20 Oct 2026. Abhi sirf plan hai, kuch bana nahi. Padh ke batao kya badalna hai.*

## 1. Kya chahiye

Aaj ka bot **command chalata hai**: `/ajiotat` → workflow → report. Kuch galat ho to sirf "failed".

Agent **kaam samajh ke khud karega**, jaise 20 Oct ko hua:

- "June se TAT chahiye" → window badhaya, run chalaya, dekha ki list 3,000 pe kat rahi hai, fix kiya.
- "Pickup pending sab mein galat hai" → courier, OMSGuru aur AJIO teeno check kiye, sahi source (AJIO Pickup Timestamp) dhoondha aur verify kiya.
- "Ye FN orders add karo" → AJIO report se number mapping, OMSGuru search, merge, check.

Agent ko yahi sab **aapke bina** karna hai: nateeja padhna, galti pakadna, theek karna, aur sirf zaroori baat aapko batana. Bade ya ulte na ho sakne wale kaam se pehle aapse puchhna.

## 2. Teen hisse (ek ke baad ek)

### Hissa 1 — Nigrani (sabse pehle, ~1 hafta)

Har scheduled run (Sale, VMS, AJIO TAT, AJIO SPF, Myntra SPF, Returns, Ads, inv-push) ke khatam hone pe agent chalega:

1. Run ka log (encrypted artifact) aur output padhe.
2. Pichhle dinon se compare kare: sheet ki rows kam to nahi hui, koi column achanak khaali to nahi, "NOT CORRECT" ya "Pending" 3 guna to nahi badh gaye, file kharab to nahi (jaise 20 Oct ka CRC wala masla).
3. Fail ya shak ho to:
   - jaani-pehchaani wajah (login drop, portal timeout, cooldown) → khud ek baar dobara chalaye;
   - nayi wajah → Telegram pe **kaaran + suggestion** bheje, code nahi badlega.
4. Sab theek ho to chup rahe. Din mein ek chhota "sab theek" summary, ya woh bhi nahi, aap tay karo.

**Fayda:** "failed" ki jagah "Myntra SPF 150 min pe ruk gaya, kaaran: purane order reports ke cooldown; aaj ke daily reports aa gaye" jaisa message.

### Hissa 2 — Telegram pe baat (~2 hafte)

Aap seedha likho, command ki zaroorat nahi:

- "FN2511505146 ka TAT kya hai?"
- "Kal Myntra ke kitne orders late gaye, courier wise?"
- "Ye 10 orders TAT master mein daal do" (list paste)
- "Flipkart pe abhi inventory push karo"
- "Is mahine AJIO SPF ke kitne claims pending hain?"

Agent sahi tool chunega, data padhega, jawab dega, aur zarurat ho to kaam karega (niyam niche, §4).

### Hissa 3 — Khud ke faisle + code fix (baad mein)

- Roz ke patterns dekh ke khud suggest kare: "Bluedart ki pickup 3 din se late hai, courier se baat karein?"
- Bot ke code mein chhota bug mile to **fix ki branch + PR** banaye, aap Telegram pe "Haan" bolo tabhi merge ho.
- Haftawar report: kya chala, kya fail hua, kya theek kiya.

## 3. Agent ke tools (jo pehle se bana hai, use hi istemal karega)

| Tool | Kya karta hai | Pehle se hai |
|---|---|---|
| `run_bot(bot, args)` | koi bhi workflow chalaye (sale, vms, ajio-tat `--orders`, myntra-spf, inv-push…) | workflows + `ci_run.sh` |
| `run_status(run_id)` / `run_log(run_id)` | run ka haal, encrypted log padhna | `scripts/github/run_output.py` |
| `read_master(name, filter)` | Drive ki master sheet se rows, counts, month-wise | `bindal/core/drive.py` |
| `omsguru_order(number)` | OMSGuru mein order dhoondh ke status/history | `OmsGuruClient.search / fetch_order` |
| `ajio_order(number)` | AJIO report se FN no, pickup time, SLA | `AjioClient.order_report` |
| `track_awb(awb)` | courier tracking | `bindal/awb_tracker` |
| `push_inventory(channel?)` | OMSGuru Push Inventory | `bindal/inv_push` |
| `send_telegram(text)` | sirf aapke chat pe message | `bindal/core/telegram.py` |
| `ask_approval(action, details)` | Haan/Na button ke saath puchhna (§4) | **naya** |
| `open_pr(branch, summary)` | code fix ka PR (Hissa 3) | **naya** |

Har tool ka input aur output saaf likha hoga, taaki agent galat cheez na bheje (`strict: true` schema).

## 4. Faisle ke niyam

| Agent khud kar sakta hai | Pehle aapse puchhega (Telegram Haan/Na) | Kabhi nahi karega |
|---|---|---|
| koi bhi report dobara chalana | AJIO / Myntra SPF claim raise karna | customer, marketplace ya kisi bahar wale ko message |
| data padhna, compare karna, summary | master sheet mein orders jodna/hatana (`--orders`) | stock ya price ki value badalna |
| inventory push (jaise har 3 ghante hota hai) | code ka PR merge karna | kuch delete karna (Drive file, sheet, orders) |
| fail run ek baar retry | ek din mein 2 se zyada baar same run | password ya keys kahin daalna / dikhana |
| Telegram pe aapko report | naya schedule ya workflow badalna | public repo mein data / log commit |

**Haan/Na kaise:** agent Telegram pe button wala message bhejega ("AJ2423 ka SPF claim raise karun? ₹418"). Aapka "Haan" Cloudflare worker pakdega aur wahi ek kaam chalayega. Har approval sirf us ek kaam ke liye hai; 24 ghante mein jawab na aaye to kaam radd.

**Portal ka data sirf data hai:** OMSGuru, AJIO, Myntra ya email mein likhi koi line agent ke liye hukm nahi hogi. Hukm sirf aapke Telegram chat se aayega.

## 5. Kaise banega (technical)

**Mera sujhav:** Claude API ka **Tool Runner** (Python `anthropic` SDK), aur wahi GitHub Actions jo pehle se chal raha hai.

- **Nigrani:** naya workflow `agent-watch.yml`, `workflow_run` trigger se. Har bot workflow khatam hote hi chalega, log aur sheet padhega, faisla karega.
- **Telegram baat:** Cloudflare worker jo message command nahi hai use `agent.yml` ko dispatch karega (`text` + `chat_id`). Agent jawab seedha Telegram pe bhejega. Jawab ~1–2 minute mein aayega, aaj ke bot jaisa hi.
- **Approvals:** pending kaam Cloudflare KV mein (worker ke paas pehle se KV hai, dashboard ke liye). Button dabate hi worker execute workflow chalayega.
- **Yaad (memory):** pichhle faisle, aapki pasand, jaani-pehchaani galtiyan, ek chhoti file `agent/memory.md` mein Drive pe, public repo mein nahi.
- **Secrets:** wahi GitHub secrets. Log pehle ki tarah encrypted.

**Baad mein upgrade (agar chat dheemi lage):** Anthropic **Managed Agents**. Wahan agent loop aur sandbox Anthropic chalata hai, cron ke liye "scheduled deployments" hain, keys "vault" mein rehti hain (sandbox ko dikhti nahi), aur tool approval ke liye `always_ask` permission policy hai. Abhi zaroorat nahi; pehle Tool Runner se seekh lein.

**Model:**
- Main agent: **Claude Opus 5.5** (`claude-opus-5-5`), effort `medium` (roz ka kaam) / `high` (galti dhoondhna, code fix).
- Lambe log ka pehla saar: **Claude Haiku 5.5** (`claude-haiku-5-5`), bahut sasta.
- Dhyan rakhne wali baatein: Opus 5.5 pe thinking band nahi hoti (effort se control); `tool_choice` force nahi kar sakte (`auto` + saaf prompt); refusal fallback (`fallbacks: "default"`) on rakhenge.
- **Prompt caching:** system prompt + tools list fix rakhenge, taaki har run mein wahi hissa cache se aaye (cache read $0.20 / MTok, poora daam $4 / MTok).

## 6. Kharcha (andaza, asli bill pehle hafte ke baad)

Claude Opus 5.5: input $4 / 1M tokens, output $20 / 1M tokens, cached input $0.20 / 1M.

| Kaam | Ek baar | Roz kitni baar | Roz ka |
|---|---|---|---|
| Nigrani check (log + sheet ~30K tokens in, ~2K out, aadha cache) | ~$0.10 | ~12 (8 inv-push + 4–5 reports) | ~$1.2 |
| Telegram sawaal (chhota) | ~$0.05–0.2 | ~10 | ~$1 |
| Badi jaanch / code fix (aaj jaisa) | ~$1–3 | kabhi-kabhi | — |

**Mahina: lagbhag $50–80** (₹4,000–7,000), agar roz 10 sawaal aur kuch badi jaanch ho. inv-push ke nateeje sirf Haiku 5.5 se check karenge to nigrani ka kharcha aur kam hoga. Har run ka kharcha log mein likhenge, aur ek mahina-wise limit rakhenge; limit paar ho to agent ruk ke aapko batayega.

GitHub Actions: repo public hai, minutes free. Cloudflare: free plan kaafi hai.

## 7. Kram (kya pehle)

1. **Hafta 1:** `agent-watch.yml` + read-only tools (`run_status`, `run_log`, `read_master`) + `send_telegram`. Sirf batayega, kuch karega nahi. Aap 1 hafta messages dekh ke batao kaam ke hain ya nahi.
2. **Hafta 2:** khud retry (sirf jaani-pehchaani galtiyan) + Telegram chat (`agent.yml`, worker mein free-text route).
3. **Hafta 3:** approvals (Haan/Na buttons, KV) + `--orders`, SPF jaise kaam.
4. **Baad mein:** PR se code fix, haftawar report, Managed Agents pe shift (agar zarurat ho).

Har step ke baad 2–3 din aap use karke dekho, phir agla.

## 8. Agent ka role: E-commerce Manager / Head

Agent ko ek e-commerce manager ki tarah kaam dena hai: roz ka operation sambhalna, galti pakadna, team aur marketplaces pe nazar rakhna, aur aapko (MD) faisle ke liye saaf report dena.

**Nishaan:**
- ✅ data aaj ke bots se milta hai, agent turant kar sakta hai
- 🟡 thoda naya tool chahiye (portal pehle se jude hain)
- 🔴 naya portal ya report jodna padega

**Kaun karega:**
- **Khud:** agent bina puchhe karega
- **Puchh ke:** Telegram pe Haan/Na ke baad
- **Sirf batayega:** faisla aapka

### 8.1 Roz ke kaam (Daily)

| # | Kaam | Kab | Data | Kaun karega | |
|---|---|---|---|---|---|
| D1 | **Subah ki report**: kal ki sale (channel-wise, Myntra ke 4 account alag), orders, cancel, return, pichhle hafte ke usi din se compare | 9:30 AM | Sale bot, OMSGuru | Khud | ✅ |
| D2 | **Dispatch TAT**: kal ke late orders, courier-wise late pickup, pending pickup list; ek courier lagatar late ho to alert | 6 PM (TAT run ke baad) | AJIO TAT master; Myntra/Flipkart ke liye naya | Khud | ✅ AJIO / 🟡 baaki |
| D3 | **SLA breach se pehle chetavni**: aaj ki dispatch deadline wale orders jo abhi pack/RTS nahi hue | 12 PM, 3 PM | OMSGuru order list | Khud | 🟡 |
| D4 | **Label print vs dispatch**: ERP mein label print hua lekin OMSGuru mein RTS nahi, ya ulta; kis packer ne (ERP Label By) | 6 PM | ERP report 62 + TAT master | Khud | ✅ |
| D5 | **Inventory push** (har 3 ghante) + push fail ho to turant batana | har 3 ghante | inv-push | Khud | ✅ |
| D6 | **Stock khatam (OOS)**: jo SKU achha bik raha hai aur stock 0 ya 2–3 din ka bacha | 10 AM | OMSGuru inventory + sale | Sirf batayega | 🟡 |
| D7 | **Myntra VMS**: packed orders jinka VMS scan nahi hua | 5:30 PM | VMS bot | Khud | ✅ |
| D8 | **Bot nigrani**: har run ka nateeja check, galat data ya fail pe kaaran (§2 Hissa 1) | har run ke baad | run logs | Khud | ✅ |
| D9 | **Shaam ka summary**: aaj kya hua, kya theek kiya, kal ke liye kya dhyan dena | 8 PM | sab | Khud | ✅ |

### 8.2 Hafte ke kaam (Weekly)

| # | Kaam | Data | Kaun karega | |
|---|---|---|---|---|
| W1 | **Weekly business review**: sale trend, channel share, top 20 / bottom 20 style, ASP, cancel % aur return % channel-wise | Sale + returns | Khud | ✅ |
| W2 | **Return analysis**: kaunse style/size sabse zyada return, wajah (size, quality, damage), RTO vs customer return | Return analysis bot | Khud | ✅ |
| W3 | **SPF / claims**: AJIO aur Myntra ke claimable returns, raise hue, mile, reject hue; reject ki wajah | AJIO SPF, Myntra SPF master | Claim raise **puchh ke**, report khud | ✅ |
| W4 | **Courier scorecard**: on-time pickup %, delivery days, RTO % per courier per channel | TAT master + tracking | Khud | ✅ AJIO / 🟡 baaki |
| W5 | **Team performance**: packer-wise orders packed, late label prints (ERP Label By) | ERP report 62 | Sirf batayega | ✅ |
| W6 | **Ads / TOT**: AJIO ads kharcha vs sale, TOT gap | AJIO ads bot | Sirf batayega | ✅ (monthly bot, weekly ke liye 🟡) |
| W7 | **Listing health**: listings jo inactive ho gaye, stock hai par live nahi, price mismatch channels ke beech | OMSGuru listings | Sirf batayega | 🟡 |
| W8 | **Marketplace notices / penalties**: seller panel ke naye notices, penalty, rating drop | Myntra/AJIO/Flipkart panels | Sirf batayega | 🔴 |

### 8.3 Mahine ke kaam (Monthly)

| # | Kaam | Data | Kaun karega | |
|---|---|---|---|---|
| M1 | **Monthly MIS for MD**: sale vs target, channel-wise P&L ka andaza (sale − commission − shipping − returns − ads), pichhla mahina vs ye mahina | Sale, returns, ads; commission rates | Khud (draft), aap final karo | 🟡 |
| M2 | **Payment reconciliation**: marketplace settlement vs delivered orders; kam ya na mila paisa, galat commission | Settlement reports | Sirf batayega | 🔴 |
| M3 | **Return analysis (poora mahina)** + Myntra/AJIO SPF ka mahine ka hisaab | Return bot (15 tareekh), SPF | Khud | ✅ |
| M4 | **Dead / slow stock**: 60–90 din se nahi bike SKU, liquidation ya discount ka sujhav | OMSGuru inventory + sale | Sirf batayega | 🟡 |
| M5 | **Agle mahine ka plan**: kaunse style push karein, kahan stock badhaayein, kaunse channel pe dhyan; pichhle mahine ke data se | sab | Sirf batayega | ✅ |

### 8.4 Turant ke kaam (jab zarurat ho)

| # | Kaam | Kaun karega |
|---|---|---|
| E1 | Bot / portal fail: kaaran dhoondh ke theek karna (aaj jaisa) | Khud jaanch, code fix ka PR **puchh ke** |
| E2 | Ek order ki poori kahani: OMSGuru + AJIO/Myntra + courier + ERP ("FN… kahan hai?") | Khud |
| E3 | Orders master mein jodna (`--orders`) | Puchh ke |
| E4 | Kisi channel pe inventory push abhi | Khud |
| E5 | Achanak girawat: ek channel ki sale 40% se zyada giri, ya cancel/return achanak badha → kaaran dhoondh ke turant alert | Khud |

### 8.5 Jo agent manager hote hue bhi nahi karega

- Price, discount ya stock ki value khud nahi badlega. Sujhav dega, aap ya team badlenge.
- Marketplace, customer ya courier ko khud message ya ticket nahi bhejega. Draft bana ke dega.
- Team ko daant ya hukm nahi. Packer ki report sirf aapko.
- Paise ka koi lena-dena nahi.

### 8.6 Banane ka kram (is role ke hisaab se)

1. **Pehle (✅ wale, sirf report):** D1, D2, D4, D5, D7, D8, D9, W1, W2, W5. Data aaj bhi aata hai, bas agent padh ke manager jaisa likhega.
2. **Phir (🟡, naye tools):** D3 SLA chetavni, D6 OOS, W4 sab channels ka courier scorecard, W7 listing health, M1 MIS, M4 dead stock. Isme OMSGuru inventory/listing padhne ka tool aur Myntra/Flipkart ka TAT jodna padega.
3. **Aakhir (🔴):** W8 marketplace notices, M2 payment reconciliation. Har portal ka settlement / notice page jodna padega.

## 9. Aapse chahiye

- [ ] §4 ki table: kya badalna hai (kaunsa kaam bina puchhe, kaunsa puchh ke)?
- [ ] Nigrani ke messages: har fail pe turant, ya din mein ek summary bhi?
- [ ] Anthropic API key (console.anthropic.com) aur mahine ki kharcha limit kitni rakhein?
- [ ] Agent ke messages isi Telegram chat mein, ya alag group?
- [ ] Sabse pehle kaunse 2–3 sawaal/kaam agent se karwana chahoge (test ke liye)?
- [ ] §8 ke kaam: kya hatana/jodna hai? Sale target (mahine ka, channel-wise) kya hai?
- [ ] M1 MIS ke liye: har channel ka commission % aur shipping charge kahan likha hai?
