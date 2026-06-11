# ⚡️ ZanZer — Marketing Information Pack

> Automated Risk Enforcement for MT5 traders, delivered through Telegram.
> Source of truth for marketing copy, positioning, and feature claims.

---

## 🎯 One-liner
**ZanZer is an AI-powered risk guardian for MT5 traders that automatically enforces your trading rules, stops you from blowing your account, and coaches your discipline — all from Telegram.**

---

## 🧭 Positioning (what makes it different)
ZanZer is **not** a signals service, not a "guru," and not an EA that trades for you. It's the opposite: a **protective layer** that saves traders from their own worst habits.

> **Most tools try to help you find trades. ZanZer protects you from yourself.**

---

## 💔 The problem it solves
The #1 reason retail traders blow accounts isn't bad analysis — it's **behavior**:
- Overtrading
- Revenge trading after a loss
- Moving / removing stop losses
- Increasing lot size emotionally
- No journaling, no self-awareness
- Breaking their own rules in the heat of the moment

ZanZer makes **discipline automatic** instead of relying on willpower.

---

## 👤 Who it's for
- Retail **forex / gold / indices** traders on MetaTrader 5
- **Funded / prop-firm traders** who *must* respect daily-loss and risk limits (breaking a rule = losing the funded account) — a very strong fit
- Traders who keep blowing accounts emotionally
- Anyone serious about trading psychology and discipline

---

## 🛡️ Core features

### 1. Automated Risk Enforcement
- Set your rules: max trades/day, max daily loss (% or $), max risk/trade, max consecutive losses, max exposure.
- ZanZer **monitors your MT5 account live** and **enforces** them — warns, locks, and can **auto-close trades** when you breach a limit.

### 2. Account Lock (the "circuit breaker")
- When you break a rule, ZanZer **locks** your trading for the day.
- **You can't unlock it on demand** — by design, to stop emotional trading. It clears the next trading day.
- Try to trade while locked? It closes the trade instantly.

### 3. Trading Psychology / Emotion Engine
- Every day starts at a **100/100 discipline score**.
- Drops on losses, off-plan trades, **revenge trades** (auto-detected), and skipped journals.
- Below **50** → trading auto-locks and you must reflect with `/explain`.

### 4. Forced Trade Journal
- Auto-prompts at **entry** (setup, emotion, plan, confidence) and **exit** (reason, mistakes, emotion, rating).
- Reminders so you can't quietly skip.
- Attach **chart screenshots** to any trade.

### 5. Performance Analytics
- `/today` · `/yesterday` · `/weekly` · `/monthly` · `/performance`
- Win rate, **profit factor, expectancy, average RR**, average hold time, best/worst pair — using **industry-standard formulas**.
- **Charts:** equity curve, drawdown, emotion-score trend.

### 6. AI Performance Coach (Hermes)
- `/coach` — an AI reviews **your own** trades, journals, and emotions and returns a short discipline/psychology review.
- **Never gives signals or advice** — only reflects on your behavior.
- Powered by OpenAI or Claude (admin-selectable).

### 7. Everything in Telegram
- Clean, tappable inline menu. Works on any phone or PC. Nothing to install.

---

## 🔒 Trust & safety (use this in marketing — it builds confidence)
- **Never executes trades, never sends signals, never gives financial advice** — you stay in full control.
- MT5 passwords stored **encrypted** (Fernet).
- Terms-of-service gate + clear risk disclaimers.
- Privacy-first: the marketing channel posts **only anonymized community stats** — never anyone's identity or P&L.

---

## 🧰 Tech foundation (credibility)
- FastAPI · MetaTrader 5 integration · PostgreSQL/SQLite · OpenAI/Claude · matplotlib charts
- VPS-hosted, always-on
- Crypto + Telegram Stars payments built in
- Admin web dashboard

---

## 💸 Pricing (current config — adjust as needed)
- **Monthly:** ~$20 · **Quarterly:** ~$50
- Pay with **crypto** (auto-confirmed) or **Telegram Stars** ⭐

---

## 📣 Marketing angles & taglines
- *"Discipline beats prediction."*
- *"We guard your capital — even from you."*
- *"The bot that won't let you blow your account."*
- *"Your automated risk manager + trading psychologist, in Telegram."*
- *"Funded trader? Never break a daily-loss rule again."*
- *"It doesn't find trades. It saves accounts."*

**Built-in content engine:** the **ZanZer Risk Lab** channel auto-posts a daily discipline report + live, anonymized "guardian in action" moments → constant automated social proof.

---

## ✋ What ZanZer does NOT do (say this loudly — it builds trust)
- ❌ No buy/sell signals
- ❌ No trading on your behalf
- ❌ No financial advice
- ❌ No get-rich promises

---

## 🤝 How it works (for a "how it works" post)
1. **/start** → accept terms.
2. **/link** → connect your MT5 account (encrypted).
3. **/setrisk** → set your rules (guided).
4. **/subscribe** or **/stars** → activate protection.
5. Trade as normal — ZanZer watches, journals, scores your discipline, and steps in when you break a rule.

**Key commands:**
`/start` `/menu` `/link` `/status` `/risk` `/setrisk` `/lock` `/journal` `/trades` `/today` `/weekly` `/monthly` `/performance` `/coach` `/explain` `/subscribe` `/stars`

---

## 🔑 Honest status notes (for internal use — keep claims accurate)
- AI coach requires an OpenAI/Claude API key configured by the admin.
- Multi-account auto-provisioning works when `AUTO_PROVISION=true` + a base MT5 install is configured; validate with 2 accounts on the VPS before onboarding many users.
- No public track record yet — market the **mechanism and discipline value**, not past returns.
