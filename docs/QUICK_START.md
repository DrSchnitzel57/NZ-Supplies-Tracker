# Quick-start guide (one page)

You need only a **browser link and an account** — no Docker, no terminal, no
technical setup. Every name, ID and number below is a synthetic example.

## 1. Sign in

```
[ Sign in ]
Username: [ staff-ava        ]
Password: [ ••••••••         ]
[ Sign in ]
```

Open the link your administrator gave you and sign in. Your username appears
at the top of every page; choose **Sign out** when you leave a shared
computer. After 30 minutes without clicking, your sign-in expires: a warning
appears at the top (“Your sign-in is about to expire”). Save your work and
sign in again in a new tab — background refresh never keeps you signed in.

![Synthetic sign-in screen: username and password fields with a Sign in
button.](../static/img/01-sign-in.png)

## 2. Issue goods (the main daily task)

```
[ Issue goods ]   ← the highlighted button at the top
Person: [ Synthetic Person A (EXT-0001) ▾ ]
Item 1: [ Hygiene / Soap Bar — Available: 12 ▾ ]  Qty [ 2 ]
Item 2: [ — Choose —                          ▾ ]  Qty [   ]
[ Issue goods ]
```

1. Open **Issue goods**.
2. Choose the person (active records only).
3. Add one or more items with quantities and choose **Issue goods**.
4. Repeating the same item just adds the quantities together.

If anything is short, **nothing is issued yet**. You will see a review page:

```
Not enough stock — review before confirming
Item               You asked for   Available now   Revised quantity
Hygiene / Soap Bar       5               3               3
```

Choose **Confirm revised quantities** to issue what is available, or go back
— your original request is kept. Stock is checked again when you confirm, so
a second review can appear if someone else just issued. Your draft stays on
the page after any error; nothing is lost and double-clicking cannot issue
twice.

![Synthetic dashboard with low-stock and recent issues.](../static/img/02-dashboard.png)

![Synthetic issuing screen: choose a person, add item rows, confirm shortages explicitly.](../static/img/03-issue-goods.png)

You can also issue from a person’s profile page (**Missionaries → a name →
Issue goods to …**). It uses the same check.

## 3. Add stock

```
Inventory → “Hygiene / Soap Bar” → [ Add stock ]
Quantity to add: [ 10 ]   Reason: [ delivery from supplier ]
```

Open **Inventory**, open the item, fill in **Add stock** with a quantity and a
reason, and choose **Add stock**. “Available” is what is on the shelf;
“Given out (net)” counts only current issues.

If the shelf count itself is wrong, use **Correct stock count**: enter what
you actually counted plus a reason (for example, “recount found a damaged
box”). The difference is recorded as a correction — it never invents issues.

![Synthetic item page: Available, net given out, Add stock and Correct stock count.](../static/img/04-stock-detail.png)

## 4. Undo a mistake

```
History → Issue to Synthetic Person A → [ Undo this issue ]
Reason: [ wrong person selected ]   [ Undo this issue ]
```

Open **History**, open the issue, give a reason and choose **Undo this
issue**. Everything in that issue goes back to Available. An issue can only
be undone once; the original record (who gave it and when, who undid it and
when) is kept.

![Synthetic reports: per-item, per-person, low-stock, stock, category, arrivals and history.](../static/img/05-reports.png)

## Tips

- Numbers refresh about every 15 seconds without touching your form. If
  “Connection: offline” appears, keep your draft and retry — nothing is sent
  in the background and no personal data is stored in the browser.
- Every field works with keyboard only (Tab to move, Enter to submit), and
  tables stack into cards on narrow screens.
- Stuck? Ask an administrator — everyday work never needs the Users or
  technical tools.
