# Decisions log

Decisions made with BFS during the build that refine or change the original
build brief. Newest first.

## 2026-10-08

| Topic | Decision |
|---|---|
| Order lists | Users can switch between **My Orders**, **All Orders** and **Historic Orders**. All Orders shows every user's submitted and cancelled orders (read only unless you own the order or are an admin). Historic Orders shows orders whose personal data has been anonymised. Drafts are only ever visible to their owner. |
| Vinyl cut to length (pricing) | Line value = length (m) × roll width (m) × trade price per m². The roll width is set per item by an admin. If no roll width is set, the line is priced as length × price per m² (that is, a width of 1 m), and the line is shown as an estimate. *Awaiting confirmation that this matches the intended rule.* |
| Rounding | Area is worked out to 2 decimal places of m². Each line value is rounded to the penny (half up). The order total is the sum of the rounded lines. |
| Retention timing | Personal data (job number, property address, requester name) is cleared **30 days after the requested delivery date**, not the order date. Once cleared, the order can no longer be amended or cancelled. |
| Anonymous ID | A different random ID is used for each order, so anonymised orders cannot be linked to one person. |
| Old drafts | Drafts not saved for 30 days are deleted automatically by the daily job. Users with drafts due for deletion within the next 5 days get an alert when they sign in. |
| Temporary codes | Items with temporary codes (duplicates and placeholders) are orderable. Incomplete items (no price or name) are not. A fresh catalogue will be uploaded once the BFS team has resolved the codes. |
| Hosting region | Render has no UK region, so Frankfurt (EU) is used. |
