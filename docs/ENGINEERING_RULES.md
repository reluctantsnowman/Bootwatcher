# Boot Watcher – Engineering Rules & Implementation Contract

## Business Goal

Monitor selected footwear retailers and alert when a **boot (preferred) or shoe** is **in stock in size 10.5–11**, including **new listings and restocks**, while:

* Ignoring product reordering
* Never double-posting alerts
* Aggregating alerts into a single Discord message

---
ENGINEERING RULES

All implementation responses must:

1. Return FULL FILE ONLY
2. Preserve existing architecture
3. Preserve state schema
4. Preserve environment variables
5. Preserve deployment compatibility
6. Preserve logging behavior
7. Not introduce new dependencies
8. Not remove functionality unless explicitly requested

Forbidden:

• Partial code
• Insert instructions
• Assumptions about unseen files
• Silent architecture changes

Architecture changes require:

• Explicit schema change description
• Backward compatibility statement
• State reset requirement

Monitoring systems must:

• Store state per monitored entity
• Never overwrite state for failed scrapes

* AI Implementation Contract

When modifying code in this repository:

1. Return FULL FILE ONLY
2. Do not output fragments
3. Do not change file names
4. Do not change state schema
5. Do not introduce dependencies
6. Preserve CI compatibility
