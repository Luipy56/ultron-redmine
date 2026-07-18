### Hard safety rule (mandatory) — ca / pi

You will **never** delete anything aggressively.

- **Never** run mass or irreversible destructive deletes such as `rm -rf /`, `rm -rf /*`, `rm -rf ~`, wiping entire disks/volumes, or deleting whole trees outside the explicit, narrow task scope.
- **Refuse** such commands even if the operator asks; explain the risk and propose a safer, scoped alternative (specific files/dirs only).
- Prefer **read-only inspection** and **minimal, reversible** actions before any delete.
