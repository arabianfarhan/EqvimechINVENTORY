# Eqvimech Inventory App Scope

## Product Goal

Build a mobile-first internal inventory application for store issue and stock control.
The first release should be simple enough for operators on the shop floor and detailed enough for managers to track stock movement, low-stock risk, and usage history.

## Primary Users

### 1. Operator / User
- Search and view available materials.
- Pick material from store.
- Mark each picked quantity against the relevant machine serial number.
- See updated balance stock immediately after issue.

### 2. Store Manager
- All operator actions.
- Deposit stock into store.
- Create and maintain item master records.
- Track history, usage, and low-stock alerts.
- View simple analytics and export records.

## Core Functional Scope

### 1. Item Master
- Item ID
- Item name
- Description
- Unit
- Current stock
- Location
- Minimum stock level
- Reorder quantity
- Active status

### 2. Mobile Item List
- Card-based list designed for mobile screens.
- Search by item ID, name, description, or location.
- Quick stock status visibility.
- One-tap action to pick material.

### 3. Pickup Workflow
- User selects an item.
- User enters quantity.
- App requires one machine serial number entry per picked unit.
- User enters purpose / usage note.
- System updates balance stock and creates history records.

### 4. Deposit Workflow
- Manager selects item.
- Manager enters deposited quantity.
- Manager adds note / remark.
- System updates stock and records the movement.

### 5. Stock Movement History
- Track issues and deposits.
- Record actor, role, quantity, machine serial number, purpose, previous stock, and balance stock.
- Filterable for manager review.
- Exportable to CSV.

### 6. Reorder Controls
- Minimum stock level per item.
- Reorder quantity per item.
- Low-stock alert view.
- Highlight low or zero stock in the item list.

### 7. Manager Dashboard
- Total active items.
- Total stock units.
- Low-stock item count.
- Out-of-stock item count.
- Issues today.
- Deposits today.
- Most-consumed items.
- Recent stock movement.

## UX Scope

### Mobile-First Principles
- Large touch-friendly buttons.
- Simple card layout.
- Minimal navigation depth.
- Clear status colors for normal, low-stock, and critical items.

### Theme Direction
- Clean industrial look.
- Light background.
- Teal / blue action accents.
- Red warning accents for low stock.
- Compact and readable typography.

## Data Rules

- Stock cannot go negative.
- Every issued unit must be traceable.
- Inactive items cannot be issued.
- History is append-only and should not be deleted from UI.
- Item master can be updated by manager without altering past history.

## Phase 1 Deliverables

1. Mobile item list with search.
2. Pick material workflow with per-unit serial numbers.
3. Deposit stock workflow.
4. Item master maintenance.
5. Low-stock alerts.
6. Manager history and dashboard.

## Deferred To Later Phase

1. Real login and password management.
2. Barcode / QR scanning.
3. Multi-store / multi-warehouse support.
4. Purchase order integration.
5. Vendor management.
6. Approval workflows.
