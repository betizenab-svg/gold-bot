# Optional: independent proof on Myfxbook (needs a computer running MT5)

`SignalCopier.mq5` copies the bot's new signals into an MetaTrader 5 account.
Linking that account to Myfxbook gives independent proof of the results.

**Untested.** The developer could not run MT5. Use a demo account first and
check every order it places.

1. Open a free demo account with your broker (MT5).
2. In MT5: File > Open Data Folder > MQL5 > Experts, copy `SignalCopier.mq5` there.
3. Open MetaEditor (F4), open the file and press Compile.
4. Tools > Options > Expert Advisors: tick "Allow WebRequest for listed URL" and add
   `https://raw.githubusercontent.com`.
5. Drag "SignalCopier" onto any chart, allow algo trading, set `RiskPercent` (1 is fine)
   and `SymbolSuffix` if your broker adds letters to names (e.g. `XAUUSDm`).
6. Keep the computer and MT5 running. Signals appear within a minute of being sent.
7. Free Myfxbook account: Add account > MetaTrader 5 > enter the demo account's
   investor (read-only) password. Make the account public and put the link on the website.
