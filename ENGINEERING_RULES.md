# Boot Watcher – Engineering Rules & Implementation Contract

## Business Goal

Monitor selected footwear retailers and alert when a **boot (preferred) or shoe** is **in stock in size 10.5–11**, including **new listings and restocks**, while:

* Ignoring product reordering
* Never double-posting alerts
* Aggregating alerts into a single Discord message

---

# Interaction Rules

### Ambiguous Requests

If the requested change affects monitoring logic or architecture and the business intent is unclear:

* Ask clarifying questions **before coding**.

### Clear Requests

If the request is clear:

* Implement directly.
* Optional improvements may be suggested **after the full file output**.

---

# Output Rules

All implementation responses must:

* Return **FULL FILE ONLY**
* Provide **complete executable code**
* Preserve **environment variables**
* Preserve **GitHub Actions / CI compatibility**
* Preserve **deployment/runtime compatibility**
* Preserve **all unrelated functionality**
* Avoid introducing **unrequested dependencies**
* Avoid removing existing logging unless explicitly requested
* Avoid interactive inputs

Forbidden:

* Code fragments
* Partial rewrites
* “Insert this section”
* “Add this below”
* References to unseen files
* Hidden assumptions about external architecture

---

# Monitoring Architecture

Monitoring must:

* Store **state per site**
* Detect changes using **stable identifiers**

Valid identifiers include:

```
product_url
product_id
sku
```

Product ordering or page layout changes **must not trigger alerts**.

---

# Size Detection Rules

An item qualifies as **alertable** only if the monitored size is in stock.

Accepted size variants:

```
10.5
10½
11
11D
```

If variant-level stock data is available, it must be used.

---

# Change Detection Logic

## New Product

A product is considered **NEW** when:

```
product_id not present in previous state
AND
target size is available
```

---

## Restock

A product is considered **RESTOCKED** when:

```
previous_state[size] = unavailable
current_state[size] = available
```

---

## Ignore Conditions

Do not trigger alerts for:

* Product reordering
* Price changes
* Cosmetic page changes
* Non-target sizes
* Previously reported in-stock items

---

# State Persistence Rules

State must:

* Persist across runs
* Be stored **per monitored site**
* Be saved **only after successful execution**

Example conceptual structure:

```
state_last_top5.json

{
  "site_a": {
    "product_id": {
      "sizes": {
        "10.5": true,
        "11": false
      }
    }
  }
}
```

State must be written **after monitoring completes successfully** to avoid corrupt state during failures.

---

# Duplicate Alert Prevention

The system must prevent duplicate alerts.

The same product **must not trigger a new alert** when:

```
product_id unchanged
size unchanged
stock state unchanged
```

Reruns of the script must not re-send previous alerts.

---

# Alert Aggregation

All detected changes must be aggregated into **one Discord message per run**.

Example format:

```
Boot Watcher Update

New Listings:
• Brand Model — Size 10.5
• Brand Model — Size 11

Restocks:
• Brand Model — Size 11
```

---

# Alert Reliability

Webhook failures must **fail safely**.

Requirements:

* Discord errors must **not crash execution**
* Monitoring must continue
* Errors should be logged

Example pattern:

```
try:
    post_to_discord()
except Exception as e:
    log_error(e)
```

---

# File and Schema Protection

The following must **not change without explicit approval**:

* File names
* State schema
* Environment variable usage
* Deployment compatibility

If a change would require altering these:

```
STOP
Ask for approval before implementing
```

---

# Execution Environment

The system is designed to run under:

* **Python 3.11**
* **GitHub Actions (ubuntu-latest)**

Dependencies allowed:

```
requests
beautifulsoup4
lxml
```

No additional dependencies may be introduced without approval.

---

# Monitoring Behavior Summary

The watcher must detect:

* New listings containing target sizes
* Restocks where target sizes become available

The watcher must ignore:

* Product ordering changes
* Cosmetic updates
* Previously reported products
* Non-target sizes

The system must:

* Persist state safely
* Prevent duplicate alerts
* Aggregate alerts into a single message
* Fail safely on webhook errors
* Maintain CI compatibility

* AI Implementation Contract

When modifying code in this repository:

1. Return FULL FILE ONLY
2. Do not output fragments
3. Do not change file names
4. Do not change state schema
5. Do not introduce dependencies
6. Preserve CI compatibility
