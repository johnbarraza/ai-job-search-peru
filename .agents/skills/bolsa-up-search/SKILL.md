---
name: bolsa-up-search
description: Search and read the authenticated Bolsa UP (Symplicity), check submitted applications, and use a stored CV for an explicitly requested application through the local CLI. Use for Bolsa UP or up-csm.symplicity.com jobs.
enabled: false
---

# Bolsa UP

Read [docs/SYMPLICITY.md](../../../docs/SYMPLICITY.md) for setup, commands, session privacy, and the distinction between the Bolsa UP form and an external employer form. The executable is `tools/pe_symplicity.py`; the saved-query workflow is `tools/pe_pipeline.py` with `portal: "bolsa-universidad"` in the profile's private `reports/busqueda/consultas.json`.

From the profile folder, use the configured `symplicity_account`. The person signs in inside Chrome; never request a password or PIN in chat or store one in the repo. Run commands for one account sequentially because they share a Chrome profile. Search without `-q` to see all listings by date. `detail` reads the actual role requirements, modality and deadline; `applications` checks what was submitted.

`upload-resume`, `delete-resume`, and `submit-application` are dry runs unless `--confirm` is passed. Send an application only when the user requested it, with a verified PDF and the exact stored CV label. If a send's immediate verification fails, check `applications` before considering another click. Some jobs open an external employer form; the CLI does not submit those. This skill is disabled for generic `/scrape` runs because the board is optional and needs a personal login; the profile pipeline uses it when configured.
