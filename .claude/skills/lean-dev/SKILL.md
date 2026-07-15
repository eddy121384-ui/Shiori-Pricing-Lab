---
name: lean-dev
description: A minimal-footprint coding discipline run before writing or editing code — climbs a seven-rung ladder (needed? → already in codebase → stdlib → native feature → installed dependency → one-liner → only then the smallest working version) before adding a line. Use when writing, generating, refactoring, or extending code — indicators, pricers, scripts, web pages, Claude Code tasks, artifacts — especially with over-build risk (new dependency, new abstraction, speculative future-proofing). Never cuts validation, error handling, security, financial correctness, or accessibility for brevity. Inspired by the open-source "ponytail" philosophy, rewritten as Eddy's own. NOT for deciding what to build (spec-tailor) or forming a market judgment (insight-engine) — governs only how much code gets written once the what is decided. 省力道:寫改程式碼前先爬七層判斷梯,能不寫就不寫、能重用就不重寫、一行寫得完就不寫十行。驗證、錯誤處理、資安、金融正確性、無障礙永遠不砍。管「該寫多少」,不是「該不該建」(spec-tailor)或「行情怎麼看」(insight-engine)。
---

# Lean-Dev｜省力道

## Why this exists｜為什麼存在

Left alone, an AI writing code tends toward the opposite of judgment: it reaches for a library when a native feature would do, wraps a one-liner in a class "for extensibility," and pads a small fix into a redesign because more code looks more thorough. Every extra line is something Eddy has to read, review, and carry as maintenance debt in a maker-checker workflow where he is the checker. The lazy senior dev — the one who's been in the trading room longer than the version control, who looks at fifty lines and says nothing before replacing them with one — isn't lazy about the problem. He's lazy about the solution, because he already knows most solutions people reach for are bigger than the problem in front of them.

放著不管,AI 寫程式碼的本能常常跟判斷力相反:平台原生功能能做的事去裝套件,一行寫得完的東西包一層 class 說是「為了擴充性」,一個小修補養成一次重構,因為程式碼多看起來比較用心。每多一行,都是 Eddy 在 maker-checker 架構裡身為 checker 要讀、要 review、要背一輩子維護債的東西。真正資深、懶得動的那種工程師懶的不是問題本身,是解法——因為他早就知道大部分人伸手拿的解法,都比眼前的問題還大。

## Top principle｜最高原則

Necessary > clever. Reused > rewritten. Native > wrapped. Small diff > impressive diff. The goal was never fewest characters — it's writing only what the task actually needs, so the size of the change matches the size of the problem.

必要 > 聰明;重用 > 重寫;原生 > 包裝;小改動 > 華麗改動。目標從來不是字數最少,是「剛好夠用」,讓改動的大小配得上問題的大小。

---

## The ladder｜判斷梯（每次動筆前,由上往下爬,卡在第一個成立的階）

Read and trace the actual code path the change touches *before* climbing — the ladder governs the solution, not the understanding. Then, in order:

先讀懂、追完這次變更牽動的真實程式碼流程,再爬梯——這把梯子管的是解法,不是理解。接著依序:

1. **需要存在嗎？｜Does it need to exist?** — Is this solving a problem the user actually has, or one that might happen someday? Speculative flexibility, unused config knobs, "just in case" parameters — cut them.
2. **codebase 裡已經有了嗎？｜Already in this codebase?** — Search before writing. A near-duplicate helper, an existing util, an established pattern in a sibling file — reuse it, don't reinvent it with slightly different naming.
3. **標準函式庫做得到嗎？｜Stdlib?** — Language/runtime built-ins before third-party packages.
4. **平台原生功能做得到嗎？｜Native platform feature?** — `<input type="date">` beats a date-picker library. A browser API beats a polyfill nobody asked for.
5. **已安裝的依賴做得到嗎？｜Already-installed dependency?** — If something's already in `package.json` / `requirements.txt` and does the job, don't add a new one for a marginally nicer API.
6. **一行寫得完嗎？｜One line?** — If a comprehension, a ternary, or a single native call does it clearly, that's the answer. ("Clearly" is load-bearing — a one-liner that needs a comment to be readable has failed this rung.)
7. **以上都不行,才寫剛好夠用的最小實作｜Only then: the minimum that works.** — Not golfed, not padded. The smallest correct implementation, sized to the actual requirement.

Stop at the first rung that holds. Don't keep climbing past a "yes" looking for a more elegant answer — elegance-hunting is itself a form of over-building.

在第一個成立的階停下。「成立」之後別再往上爬找更漂亮的答案——找漂亮本身就是一種過度建造。

---

## The floor — never on the chopping block｜地板：永遠不砍

Lazy about the solution, never negligent about correctness or safety. The ladder above governs *how much* gets written; the following are not "how much," they're the definition of "correct," and cutting them is a defect, not efficiency:

- **輸入驗證與邊界處理｜Input validation & boundary handling** — hostile inputs, missing/stale data, malformed values.
- **錯誤處理｜Error handling** — failures must fail visibly, not silently swallow.
- **資安與權限｜Security & auth boundaries** — never trimmed for brevity.
- **無障礙｜Accessibility** — when the output is user-facing UI.
- **金融正確性與稽核鏈｜Financial correctness & audit trail** — for pricer, valuation, risk, trade-signal, or market-data code: unit correctness (yield vs. price direction, day-count, time zone, valuation date), reproducibility, and a clear human sign-off point are never simplified away. When Eddy is the checker in a maker-checker loop, the diff must stay legible enough for him to actually check it — a "smaller" diff that's harder to review is not a win.

If a rung on the ladder would require cutting anything on this list to stay small, that rung doesn't hold — move up to the next one instead.

如果梯子上某一階要靠砍掉這份地板才能維持「小」,那一階不成立——換下一階。

---

## When it fires｜何時觸發

Runs automatically whenever code is being written, generated, refactored, or extended — Claude Code tasks, artifacts, scripts, Pine Script indicators, pricer logic, web pages, config files. Doesn't need to be invoked by name.

不需要使用者特別呼叫,只要在寫、改、擴充程式碼,就先過這道梯子。

Applies with extra weight when a request smells like over-build risk: "make it flexible for the future," "add a config option for X," "in case we need Y later," a new dependency for something small, a new abstraction layer, a rewrite where a patch would do.

當請求帶著過度建造的味道時——「先做彈性一點」、「加個設定選項以防萬一」、「以後可能需要」、為小事引入新依賴、新增抽象層、能補丁卻要重寫——這條梯子權重更高,要更用力守。

---

## Self-check before writing code｜動筆前,默默自檢

1. Am I about to add a dependency/abstraction/config the task didn't ask for?
2. Is there existing code in this project I should reuse instead?
3. Does a native/stdlib feature already cover this?
4. Is this diff sized to the actual problem, or bigger because bigger looks more thorough?
5. Did I cut anything on the floor list to make this smaller? (If yes — put it back, then re-climb.)
6. Would Eddy, reading this diff as checker, immediately see what changed and why?

---

## Boundary with spec-tailor and insight-engine｜與 spec-tailor、insight-engine 的邊界

Three different jobs, same underlying instinct — cut the bloat, keep the load-bearing parts — applied at three different moments:

- **spec-tailor** governs the *document* before code exists: is the idea formed, is the spec sized right, no invented architecture in the writeup.
- **lean-dev** (this skill) governs the *code* once the spec/task is already decided: how much gets written, which rung on the ladder, what never gets cut.
- **insight-engine** governs *market judgment*: forming a stance on price, rates, or a trade — unrelated to how code gets written.

If the ask is "help me figure out what I even need," that's spec-tailor. If the ask is "write/fix this code," that's lean-dev. If the ask is "what do you think happens next," that's insight-engine. They don't overlap; pick the one the moment actually calls for.

三個技能同一種本能——刪臃腫、留承重——用在三個不同時刻:spec-tailor 管「動筆寫程式前的規格文件」,lean-dev 管「規格定了之後,程式碼該寫多少」,insight-engine 管「行情怎麼看」。念頭沒成形找 spec-tailor;要寫/改程式碼找 lean-dev;要形成市場判斷找 insight-engine。

---

## Output style｜輸出風格

No narration of the ladder in the delivered code or its comments — the climbing happens before writing, silently. If a rung choice is non-obvious (e.g., skipping a seemingly-reasonable library in favor of a native feature), a one-line note outside the code block is enough — not a paragraph defending the decision.

程式碼本身或註解裡不寫爬梯過程——梯子在動筆前爬完,安靜地爬。若某個選擇不直覺(例如捨棄看似合理的套件、改用原生功能),code block 外一行說明就夠,不必寫一段替決定辯護。
