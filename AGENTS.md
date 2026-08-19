# Repository Instructions

## Git safety

- Do not run `git commit`, `git push`, `git tag`, publish, or deploy unless the user
  explicitly requests that exact action in the current conversation.
- Requests to edit, fix, update, finish, or verify files do not imply permission to
  commit or push.
- Preserve existing user changes and report the working-tree status after edits.

## Documentation boundaries

- The root `README.md` is project-specific. Keep the current deployment topology,
  repository entry points, active network parameters, and project-specific known
  issues there.
- `docs/Go2W_Development_Guide.md` is a general Go2-W development guide. Use
  placeholders for site-specific Wi-Fi IPs, SSIDs, MAC addresses, paths, and
  credentials. Do not copy the current project's deployment state into it.
- Framework-local README files should document only their own interfaces, setup,
  configuration, and operation.

## Credentials and device data

- Never store real passwords, access tokens, private keys, or other authentication
  secrets in tracked files. Use placeholders or environment variables.
- Before changing a documented device address, update matching runtime defaults or
  explicitly report any deliberate mismatch.
