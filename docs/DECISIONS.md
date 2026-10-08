# Decisions log

Decisions made with BFS during the build that refine or change the original
build brief. Newest first.

## 2026-10-08

| Topic | Decision |
|---|---|
| Order lists | Users can switch between **My Orders**, **All Orders** and **Historic Orders**. All Orders shows every user's submitted and cancelled orders (read only unless you own the order or are an admin). Historic Orders shows orders whose personal data has been anonymised. Drafts are only ever visible to their owner. |
| Vinyl and flooring (pricing) | The person ordering enters the **exact length and width** to be cut; stores cut to size. Value = length × width (m², 2 decimal places) × trade price per m². Wastage is disregarded. These items use the Area measure type. (Replaces an earlier roll-width proposal, which was not adopted.) |
| Rounding | Area is worked out to 2 decimal places of m². Each line value is rounded to the penny (half up). The order total is the sum of the rounded lines. |
| Retention timing | Personal data (job number, property address, requester name) is cleared **30 days after the requested delivery date**, not the order date. Once cleared, the order can no longer be amended or cancelled. |
| Anonymous ID | A different random ID is used for each order, so anonymised orders cannot be linked to one person. |
| Old drafts | Drafts not saved for 30 days are deleted automatically by the daily job. Users with drafts due for deletion within the next 5 days get an alert when they sign in. |
| Temporary codes | Items with temporary codes (duplicates and placeholders) are orderable. Incomplete items (no price or name) are not. A fresh catalogue will be uploaded once the BFS team has resolved the codes. |
| What retention clears | Besides the requester name, job number and address, the job also clears **special instructions** (often names, phone numbers or key codes) and the link to the user account. The same details are removed from the order's history and email log. Cancelled orders are anonymised on the same timetable. |
| Order email Reply-To | Order emails to stores carry the requester's email address as Reply-To, so stores can reply directly. Recipients are unchanged: stores@bfsuk.org only. |
| Email failure alert | A failed send shows the user a warning and a red count on **Emails** in the admin top bar. Admins are not emailed, because a mail fault would usually stop that email too. |
| Hosting region | Render has no UK region, so Frankfurt (EU) is used. |
