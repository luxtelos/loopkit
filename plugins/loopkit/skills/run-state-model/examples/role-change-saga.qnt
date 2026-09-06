// role-change-saga.qnt — the same FIX as role-change-saga.fizz,
// in Quint, so the two syntaxes can be compared side by side.
// Expected verdict: PASS.
// CHECK-ARGS: --invariant=safety --max-steps=8 --max-samples=2000

module roleChangeSaga {
  pure val USERS = Set("u1", "u2")
  pure val START = Map("u1" -> "admin", "u2" -> "accountant")

  var descope: str -> str   // authoritative: login adopts this
  var db: str -> str        // the firm's role_assignments row
  var lastStatus: int

  action init = all {
    descope' = START,
    db' = START,
    lastStatus' = 0,
  }

  def keepsAnAdmin(target: str, newRole: str): bool =
    USERS.filter(u => (if (u == target) newRole else db.get(u)) == "admin").size() >= 1

  // The fixed route: a rejected request writes nothing at all.
  action patchRole = {
    nondet target = USERS.oneOf()
    nondet newRole = Set("admin", "accountant").oneOf()
    val ok = keepsAnAdmin(target, newRole)
    all {
      descope' = if (ok) descope.set(target, newRole) else descope,
      db' = if (ok) db.set(target, newRole) else db,
      lastStatus' = if (ok) 200 else 422,
    }
  }

  // The victim logs in; Descope is believed, so the DB row is reconciled to it.
  action login = {
    nondet who = USERS.oneOf()
    all {
      db' = db.set(who, descope.get(who)),
      descope' = descope,
      lastStatus' = lastStatus,
    }
  }

  action step = any { patchRole, login }

  val firmKeepsAnAdmin = USERS.exists(u => db.get(u) == "admin")

  val rejectionWritesNothing = lastStatus != 422 or descope == db

  // Checked as one conjunction so a single --invariant covers both.
  val safety = firmKeepsAnAdmin and rejectionWritesNothing
}
