# Companion Studio maintenance

This file applies to this public source tree. Follow the user's requested scope and preserve user data.

## Read first

- Read `README.md`, `docs/ARCHITECTURE.md`, and the relevant `docs/AGENT_RUNTIME.md`, `docs/TOOL_SYSTEM.md`, or `docs/WORKBENCH.md` before substantial edits.
- Use `docs/DEVELOPMENT.md` for locked dependencies, checks, isolated profiles, and the distinction between unit and real GUI tests.
- Keep changes local and reviewable. Do not combine broad cleanup with unrelated behavior changes.

## Data and edition ownership

- Development profile: `.runtime/desktop`. Use an independent backend port when a production instance is running.
- Do not import another edition's code, profiles, credentials, characters, voice references, model weights, or dependency links.
- Never delete, reset, migrate, or overwrite a user's database, profile, API configuration, character card, or voice settings without specific authorization and a backup plan.
- Stop only the confirmed process tree owned by this instance. Do not kill all processes sharing an executable name.
- Runtime data, secrets, logs, local test reports, dependency directories, and generated build files must not be committed or redistributed as source.

## Implementation constraints

- Backend owns model calls and sensitive configuration; frontend must not receive API keys.
- Preserve permission checks, resource ownership, cancellation, request/session identity, and compatibility of public IPC/API and stored data.
- Workbench maintenance must preserve coordinate calculations, numeric thresholds, rAF ordering, pointer capture/cleanup, scroll ordering, layout keys/versions, and stream event order unless the task explicitly requires a behavior change.
- Do not mechanically add Hook dependencies or refactor stable native subscriptions without checking their lifecycle and current-ref design.
- Explain complex algorithms, concurrent state, side effects, and non-obvious compatibility boundaries. Comments should state reasons and actual guarantees.
- Use locked Python requirements and `npm ci`; do not upgrade dependencies incidentally or copy another project's environment.
- Redistribute only authorized character, voice, image, audio, model, and upstream material. User-provided files do not imply public redistribution rights.

## Evidence and release

- Run the relevant Ruff, pytest, TypeScript, ESLint, Prettier, Electron regression, and build gates. Fix errors at their cause; do not remove checks to obtain a pass.
- Unit checks do not prove real GUI behavior, renderer FPS, Windows app control, voice playback, or model quality. Report what actually ran, its limits, and any skipped checks.
- Real paid model, voice, or external coding-account requests require authorization. Prefer local mocks for ordinary regression.
- Build from audited public source into a new isolated `build/<BuildId>` directory. Generated metadata belongs in staging, not tracked source.
- Do not publish user profiles or local archives. Follow `docs/RELEASE_SECURITY.md` and `docs/RELEASING.md` for audited artifacts and checksums.
- A successful build or draft Release does not complete Windows installation and user-data-preservation acceptance. Do not bypass the final human release confirmation.
- Do not operate Git remotes, create tags, publish Releases, or upload artifacts unless authorized by the user's task. Keep source, build artifacts, and local reports clearly distinguished.
