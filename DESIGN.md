# FitManager Customer Onboarding Dashboard - Design Document

## 1. Data Architecture
The architecture is designed to unify data from Zoho CRM, Zoho Billing, Zoho Inventory, and GoHighLevel (GHL) into a cohesive onboarding view.

### Automatic Data Ingestion
- **Accounts/Contacts:** Synced from Zoho CRM (Accounts/Contacts modules).
- **Training Progress:** Synced from GHL via Cloudflare Worker Webhook to `Onboarding_Module`.
- **Hardware Status:** Synced from Zoho Inventory (Shipment status).
- **Billing Status:** Synced from Zoho Billing (Subscription status).
- **Support Activity:** Pulled from Zoho Desk (Ticket status).

### Manual Entry Requirements
- **Phase Transitions:** Manual or automated via workflow when a milestone is hit.
- **Blocker Logging:** Manual entry by the Onboarding Owner.
- **Risk Assessment:** Periodic manual update or automated calculation based on "Days in Phase" and "Last Activity."

### API Field Mapping (Key Mappings)
- `Account_Name` -> `Accounts.Name`
- `Deal_Look_Up` -> `Deals.Deal_ID`
- `Training_Last_Synced` -> `GHL_Webhook_Timestamp`
- `Actual_Go_Live_Date` -> `Deals.Closing_Date` (if applicable) or custom date.

## 2. New Custom Fields Needed (`Onboarding_Module`)
The following fields must be added to the `Onboarding_Module`:

### Timeline & Phase Tracking
- `Phase_1_Start_Date` (Date/Time)
- `Phase_1_End_Date` (Date/Time)
- `Phase_2_Start_Date` (Date/Time)
- `Phase_2_End_Date` (Date/Time)
- `Phase_3_Start_Date` (Date/Time)
- `Phase_3_End_Date` (Date/Time)
- `Last_Internal_Activity` (Date/Time)
- `Last_Customer_Interaction` (Date/Time)

### Risk & Blockers
- `Health_Score` (Number: 1-100)
- `Risk_Status` (Picklist: On-Track, At-Risk, Overdue, Stalled)
- `Primary_Blocker` (Multi-line Text)
- `Blocker_Type` (Picklist: Internal, External, Technical, Training, Billing)
- `Is_Waiting_On_FitManager` (Boolean)
- `Is_Waiting_On_Customer` (Boolean)

### Progress Tracking
- `Total_Tasks_Count` (Number)
- `Completed_Tasks_Count` (Number)
- `Onboarding_Days_Elapsed` (Formula: `Today` - `Start_Date`)
- `Days_In_Current_Phase` (Formula)

## 3. Timeline Event Schema
**Recommendation: Separate Custom Module (`Onboarding_Events`)**

*Why?*
- **Zoho Notes** with tags can become messy and difficult to aggregate for "Average time per phase" reporting.
- **A Custom Module** allows for cleaner relational data, specific date/time fields, and easy aggregation for the "Linear Timeline" visualization.

**Schema:**
- `Event_ID` (Auto-Number)
- `Onboarding_ID` (Lookup -> `Onboarding_Module`)
- `Event_Type` (Picklist: Kick-off, Phase Change, Training Milestone, Hardware Shipped, Support Ticket Created, Doc Signed)
- `Timestamp` (Date/Time)
- `Description` (Text)
- `Owner_ID` (Lookup -> `Users`)
- `Source` (Picklist: Manual, Webhook, CRM_Workflow)

## 4. Dashboard Component Design

### Individual Customer Card
- **Header:** Account Name | Segment | Owner | Health Score (Color coded: Green/Yellow/Red)
- **Status Bar:** Progress bar showing `Completed_Tasks_Count` / `Total_Tasks_Count`.
- **Contextual Info:** Current Phase, Days in Phase, Next Activity.
- **Alerts:** "Waiting on [FitManager/Customer]" highlighted in red if blocked.
- **Timeline Widget:** A horizontal linear timeline showing icons for major events (Kick-off, Phase 1 Start, etc.).

### Main Dashboard View
- **Top Row (KPIs):** Avg. Onboarding Time, % Overdue, Active Onboardings, Avg. Days per Phase.
- **Middle Section (Table):** Filterable list of all customers with:
    - Status (Stalled / Progressing / Overdue)
    - Days Elapsed
    - Next Milestone Date
- **Side Panel (Alerts):** "High Priority" list (At-Risk/Overdue records).
- **Filters:** Owner, Phase, Risk Level, Segment, Last Activity Date.

## 5. Automation Design
### Automatic Timeline Events
- **Deal Won:** Create `Onboarding_Module` record + Log "Deal Won" event.
- **GHL Webhook:** Log "Training Milestone" event + Update `Training_Completion %`.
- **Inventory Update:** Log "Hardware Shipped" event when status changes to 'Shipped'.
- **Desk Ticket:** Log "Support Ticket Created" event when a ticket is linked to the Account.
- **Phase Change:** Automatically timestamp the start/end of a phase when the `Onboarding_Stage` picklist changes.

### Manual Logging
- **Blocker Entry:** Owner logs specific reason for a "Stalled" status.
- **Customer Interaction:** Logged via SalesIQ or manual entry for non-chat interactions.

## 6. Reporting Components
- **Conversion Chart:** Time to "Live" vs. Target Date.
- **Bottleneck Analysis:** Chart showing average days spent in each phase (Phase 1 vs Phase 2 vs Phase 3).
- **Risk Distribution:** Pie chart of Risk Status (On-Track vs At-Risk vs Overdue).
- **Ownership Load:** Bar chart showing number of active onboardings per Owner.

## 7. Implementation Approach
**Recommended: Hybrid Approach (Standalone Web App + Zoho CRM)**

- **Why?** Zoho CRM's dashboarding is limited for complex visualizations (like linear timelines and custom risk scoring).
- **Phase 1 (Infrastructure):** Add new fields to `Onboarding_Module` and create the `Onboarding_Events` custom module.
- **Phase 2 (Automation):** Setup Cloudflare Workers and Zoho Workflows to populate the new fields and event module.
- **Phase 3 (Frontend):** Build a React/Next.js dashboard that queries Zoho CRM via API. This allows for the "Linear Timeline" and "Health Score" UI components that Zoho's native UI cannot support.

## 8. Data Source Mapping
| Event Type | Source Module | Source Field / API | Trigger Type |
| :--- | :--- | :--- | :--- |
| Kick Off | Deals | `Closed_Won` | Workflow |
| Training Progress | GHL / Webhook | `Training_Last_Synced` | Webhook |
| Hardware Ship | Inventory | `Shipment_Status` | Sync |
| Phase Change | Onboarding | `Onboarding_Stage` | Workflow |
| Support Issue | Desk | `Ticket_ID` | Sync |
| Billing Issue | Billing | `Subscription_Status` | Sync |
| Doc Signed | Zoho Sign | `Signature_Status` | Webhook |
| Manual Blocker | Onboarding | `Primary_Blocker` | Manual |
