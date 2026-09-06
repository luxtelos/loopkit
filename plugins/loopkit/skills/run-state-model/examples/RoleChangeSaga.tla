\* RoleChangeSaga.tla — the same FIX as role-change-saga.fizz,
\* in TLA+, so all three syntaxes describe one system.
\* Expected verdict: PASS. TLC reads RoleChangeSaga.cfg from cwd.
---- MODULE RoleChangeSaga ----
EXTENDS Integers, FiniteSets

Users == {"u1", "u2"}
Roles == {"admin", "accountant"}

VARIABLES descope, db, lastStatus

Start == [u \in Users |-> IF u = "u1" THEN "admin" ELSE "accountant"]

Init == /\ descope = Start
        /\ db = Start
        /\ lastStatus = 0

KeepsAnAdmin(target, newRole) ==
  Cardinality({u \in Users : (IF u = target THEN newRole ELSE db[u]) = "admin"}) >= 1

\* The fixed route: a rejected request writes nothing at all.
PatchRole ==
  \E target \in Users, newRole \in Roles :
    IF KeepsAnAdmin(target, newRole)
      THEN /\ descope' = [descope EXCEPT ![target] = newRole]
           /\ db' = [db EXCEPT ![target] = newRole]
           /\ lastStatus' = 200
      ELSE /\ UNCHANGED <<descope, db>>
           /\ lastStatus' = 422

\* The victim logs in; Descope is believed.
Login ==
  \E who \in Users :
    /\ db' = [db EXCEPT ![who] = descope[who]]
    /\ UNCHANGED <<descope, lastStatus>>

Next == PatchRole \/ Login

FirmKeepsAnAdmin == \E u \in Users : db[u] = "admin"

\* A rejected request must write nothing. Violates at depth 1 in the buggy
\* module — a sharper witness than waiting for the admin count to reach 0.
RejectionWritesNothing == (lastStatus = 422) => (descope = db)

====
