# Jev in the Amul farmer assistant: summary

**Subject:** Jev in the Amul assistant: about 0.7 seconds faster per question, same safety

We tested a faster way for the Amul farmer assistant to decide what to look up. Today, GPT-4.1 is called twice for every question. With Jev, a small, fast model from TypeSafe, one of those calls is replaced by quick multiple-choice decisions:

- **Today:** GPT-4.1 decides what to look up (vet advice, mandi prices, milk records, bookings...), then GPT-4.1 writes the answer.
- **With Jev:** Jev makes the decision; GPT-4.1 only writes the answer.
- **Result so far:** deciding drops from about 1.3 s to 0.6 s. Writing the answer takes the same time either way, so the farmer gets the first word about 0.7 seconds sooner. The AI cost per question roughly halves.
- **Safe by design:** if Jev is unsure, the old way answers. Bookings and loans need the farmer's explicit yes. It is off by default in production.

It is not ready to switch on yet: the accuracy numbers are encouraging but not proven.

- **Lab results:** Jev picked the right lookups on 14 of 14 test questions, the old way on 13 of 14. But these are the same questions we tuned Jev on, and the numbers come from small samples on one laptop.
- **Next:** test on 50+ fresh questions from real farmer logs, rate the answers side by side, then run Jev silently alongside live traffic ("shadow mode") before turning it on.
- **Details:** [plain-language guide](https://github.com/knaig/amul-oan-api/blob/main/docs/JEV_EXPLAINED.md) · [code](https://github.com/knaig/amul-oan-api)
