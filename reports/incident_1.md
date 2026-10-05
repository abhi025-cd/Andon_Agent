# 8‑D Problem‑Solving Report  

**Incident ID:** 1  
**Station:** ST‑040  
**Signal:** torque_nm  

---

## D1 – Team Formation  
**Status:** Pending – requires human input  

---

## D2 – Problem Description  
- At **2026‑10‑04T10:26:00+00:00** the torque signal at ST‑040 triggered a **TORQUE_LOW** warning alarm.  
- Monitoring data (n = 8, mean = 40.79 Nm, std = 1.415 Nm) showed a **negative slope per pass of –0.299** and a **drift_suspected** verdict with **5 NOK** out of 8 samples.  
- The drift occurred **16 min after a tool change** logged at **2026‑10‑04T10:10:00+00:00** (nut‑runner tool **NUT‑RUNNER‑07**, operator **T‑114**).  

---

## D3 – Containment (Proposed)  
A containment proposal has been generated covering the period **2026‑10‑04T10:10:00+00:00** to **2026‑10‑04T10:26:00+00:00**.  

- **window_basis:** latest tool change at **2026‑10‑04T10:10:00+00:00**  
- **n_vins:** 12  
- **n_nok:** 5  

**Proposed actions (pending approval):**  
1. **Hold VINs** – status: pending_approval.  
2. **Stop station ST‑040** – reason: vehicles arriving after this time are also at risk until the cause is fixed; status: pending_approval.  

*Note: The containment actions have not been executed; they await human approval.*

---

## D4 – Root Cause Analysis  
- **Root cause:** tool_change_misconfiguration  
- **Evidence:**  
  1. Tool change logged at 2026‑10‑04T10:10:00 (16 min before drift).  
  2. Drift‑suspected verdict with negative slope.  
  3. TORQUE_LOW alarm raised at drift time.  
- **Confidence:** high  

---

## D5 – Corrective Action(s)  
- **Inspect and recalibrate** the newly installed nut‑runner tool (**NUT‑RUNNER‑07**) to ensure correct torque settings.  

---

## D6 – Verification of Effectiveness  
**Status:** Pending – requires human input  

---

## D7 – Preventive Measures  
**Status:** Pending – requires human input  

---

## D8 – Closure and Lessons Learned  
**Status:** Pending – requires human input  