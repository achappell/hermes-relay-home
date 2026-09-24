# Approved delivery contract — 23 September 2026

This course correction supersedes conflicting historical planning clauses. Existing implementation evidence keeps its recorded scope. It does not establish completion of new acceptance. Dates are unscheduled pending estimation.

## Standard Hermes is an unmodified dependency

> Standard Hermes — The unmodified upstream Hermes agent at a recorded supported release or commit, using its supported configuration and interfaces. Both HomeBridge and direct Standard Hermes mode use this agent. A feature must not require an agent patch, fork-only endpoint, or custom agent protocol extension. HomeBridge and clients may adapt supported interfaces without changing Hermes. When those interfaces cannot support a feature, the product reports it as unavailable or explicitly defers it; preserving feature parity with the modified agent is not required.
>
> Standard Hermes Channel — A supported session or response interface provided by that Standard Hermes baseline. Its verified capabilities determine what the client may offer. The presence of a client control or a passing fake-server test does not establish agent support.

## Explicit connection choice on personal clients

> TUI, iOS, macOS and Android offer an explicit setup choice: HomeBridge or Standard Hermes. HomeBridge mode connects through HomeBridge to unmodified Standard Hermes and enables supported household features. Standard Hermes mode connects directly to unmodified Standard Hermes without requiring HomeBridge; it provides only capabilities verified for that client and supported agent baseline.
>
> HomeBridge setup uses Home enrollment and Home-issued client credentials. Standard setup uses the agent’s supported connection and authentication mechanism; it does not request a Home pairing code or require Home credentials. Each mode stores its connection and credentials separately using appropriate platform storage. Secrets must not appear in ordinary logs or history.
>
> Puck, Touch and shared W/K browser room surfaces use HomeBridge in this release. Direct Standard mode on these room surfaces is outside the current scope.
>
> The selected mode remains visible in connection settings. Unavailable features are explained without offering controls that cannot work. A HomeBridge outage leaves that connection disconnected; it never triggers a switch or fallback suggestion. Changing modes is a deliberate settings action that runs the appropriate setup. Detailed session and history behavior is defined in the session rules.

## Replace legacy pairing with Home-owned enrollment

> HomeBridge mode uses one Home-owned enrollment system. An authenticated Home pairing page provides a QR/copyable link or a short code plus Home address. The personal client submits its request, displays its confirmation code, and waits for approval. The approver verifies the device identity and confirmation and grants allowed Profiles. Once approved, the client securely stores its Home credential, renews it automatically when required, and obtains conversation access for an approved Profile without an operator supplying tokens or session handles.
>
> Preserve NW-17’s recorded ownership decisions: shared Profiles may be granted by the Home administrator; owned Profiles require owner approval, with the recorded administrator bootstrap for the first client. Pairings remain keyed per Home. Personal-client admission requires no Room, wake mapping or acoustic arbitration. Room devices use the same Home credential authority with their own room/wake/tap admission rules; iOS and Android administration uses that authority rather than a second pairing system.
>
> The Home page is the initial trusted approval surface. Standard-only setup does not use this enrollment flow. Existing NW-02 credentials, renewal and revocation machinery is reused. Legacy pairing UX, operator-supplied ordinary conversation handles and the modified-agent voice-session integration are removed at the approved cutover. NW-17’s instruction to retain the legacy voice-session path as a supported rollback route is superseded; deployment recovery is addressed separately in the retirement rules.

## Required migration capabilities and explicit feature limits

| Capability | HomeBridge personal clients: TUI, iOS, macOS, Android | Direct Standard: same four clients | HomeBridge room devices: Puck, Touch, W/K |
| --- | --- | --- | --- |
| Authorized setup and connection | Required: Home pairing and Profile grants | Required: supported Standard authentication; no Home dependency | Required: Home device and room admission |
| Typed conversation and streamed response text | Required on all four | Required on all four | Surface-appropriate display; Puck has no text display requirement |
| Microphone input and spoken responses | Required for migration acceptance | Optional for initial reduced release; offer only with verified client and upstream support | Required for the accepted voice surface |
| Stop, recovery and no replay | Required; interrupt offered work and stop local capture/playback | Required truthful recovery/no replay; expose remote interruption only when verified; local stop always applies to enabled capture/playback | Required for supported capture/playback/turn lifecycle |
| Intentional local conversation history | Required with existing opt-in/privacy rules | Required with the same privacy rules, scoped to the direct connection | No new general history browser requirement |
| Household Profile grants and device administration | Home Profile grants; device administration required on iOS and Android | Unavailable; Standard's own session identity is not a Home Profile grant | Home-managed room/wake/tap policy |
| Home diagnostics, Watch, household notifications and shared artifacts | Later epics; existing supported capabilities may remain available | Home-specific features unavailable | Later surface-specific integration |

> Epics 1–4 close only when their required end-to-end capabilities work on the accepted surfaces using unmodified Standard. An upstream limitation on a required capability triggers an explicit scope decision; it does not silently turn a failed acceptance criterion into done.
>
> Synchronized spoken-word highlighting is not required for migration. Where verified timing is absent, show transcript/response text and audio progress honestly without implying word alignment. FR-23/24 and SM-7 apply only to verified supported timing paths; report timing unavailable elsewhere.
>
> Advanced typed choose/explore interactions, protected-input UI, household notifications, shared artifacts and attachments remain in their ordered later epics. Each requires verified supported interfaces before being promised. Missing upstream support permits an explicit deferred disposition; it never permits modifying Hermes. Existing working features are preserved at their evidenced scope.
>
> Unsupported interactions must resolve visibly and safely: explain the limitation, decline/cancel through a supported operation when available, or end/report the blocked turn without replay. Do not silently approve an action, leak protected input into ordinary chat, or leave the UI pretending the turn can proceed. This baseline failure behavior remains required even when advanced prompt UI is deferred.

## Client-owned sessions and recovery without replay

> Personal clients expose deliberate New conversation and Resume actions. A new TUI launch starts a new session by default; explicit continue/resume selects an existing one. Mobile/desktop clients may reopen their selected conversation only when its connection, identity and authorization still match; otherwise they ask the user to choose. A network reconnect is recovery of the existing conversation, not a request to start a new one.
>
> In Home mode, a paired personal client may list and resume the approved Profile's sessions, including sessions started on another authorized client or room device. Home verifies the grant and keeps upstream session identifiers behind scoped references. An active session already controlled by another claim is reported busy; two clients do not drive it simultaneously. Home does not end personal conversations merely because the user pauses. Closing the client connection releases its claim while leaving the Standard session available for later authorized resume.
>
> Preserve NW-17's recorded client claim defaults: eight active claims per device and 120 seconds of reconnect grace, configurable by Home. These rules apply to personal clients; room wake/tap idle and admission rules remain separate. Claim expiry is not deletion of the stored Standard session.
>
> Resume and reconnect use verified supported Standard operations. If continuity cannot be established, show that explicitly and offer a deliberate new conversation. Never resend a turn that may have reached Hermes; preserve and display its uncertain outcome until resolved or explicitly left behind. Recovered history must not be mistaken for a command to replay prior messages.
>
> Direct Standard clients offer supported session operations scoped to the configured Standard connection and identity. Home Profile grants, room-session access and cross-device history are not promised by direct mode. Missing optional listing/resume support is reported truthfully; basic text conversation remains required under the capability matrix.
>
> Changing mode, Home, Standard endpoint or Profile is deliberate reconfiguration. Resolve or explicitly leave an active/uncertain conversation before changing its connection. Keep local history bound to its original mode, endpoint and identity; retain permitted history for viewing, but do not copy transcripts, credentials or session references into the new connection automatically. A mode switch does not imply that the same conversation can continue there. Local history retention still follows the user's privacy settings.

## Finish the replacement and retire the old system

> Reuse migration Story 9 as the Home-owned cutover and retirement record under Epic 4. Each delivery repository owns its corresponding removal and acceptance work. Inventory the legacy pairing screens/routes, fork-only adapters, settings, launch services, credentials, scripts and documentation before removal; distinguish these from reusable Standard adapters and current Home credential machinery.
>
> The migration release requires TUI, iOS, macOS and Android in both approved modes, and Puck, Touch and W/K in Home mode, to meet the setup, capability and session acceptance above. Record the exact unmodified upstream baseline and deployed code provenance. Verify setup, required text/voice behavior, supported stop, disconnect/recovery, renewal/revocation in Home mode, and honest unsupported states. Previously recorded evidence may be reused when it applies to the exact contract and build; historical done labels alone do not close new acceptance.
>
> Pilot and replace surfaces in controlled steps. Preserve intentional history and configuration data; require clear re-pairing where credential conversion cannot be supported. Retire the old system after replacement acceptance, then remove its supported code/configuration paths and stop obsolete services and invalidate obsolete credentials. Keep historical source and evidence for traceability. No supported installation may still require the modified Hermes agent or old pairing method.
>
> Deployment recovery restores a verified compatible Home/client/Standard build and recoverable configuration/data. It must not silently switch a user's mode, replay a turn, or restore fork dependence as the supported product. If no compatible recovery build exists, pause the rollout and leave the affected surface explicitly unavailable until repaired. Prepare and verify the recovery procedure before live cutover.
>
> Epic 4 requires repeatable installation, start/restart, upgrade and recovery instructions for the actual household deployment and a distributable signed macOS build. Broader installation in other homes, additional host operating systems and automatic updater work may follow later and do not block household cutover. Keep their NW-15/MACOS-DIST story identities and explicit later-release scope under Epic 4; split acceptance where a story mixes required and later work. Migration-release completion is distinct from completion of all later work in that epic.

## Working order and authority

1. Connect personal clients through HomeBridge
2. Use personal clients with Standard Hermes alone
3. Set up and use room voice devices
4. Retire the legacy setup and deploy the replacement
5. See room activity and departure reminders
6. Observe and diagnose the household system
7. Make choices and provide protected input
8. Receive useful notifications
9. Work with shared artifacts and attachments

The owning story index stores product_epic, release_scope and dependencies. Historical numeric story prefixes are stable identifiers, not current epic parents. Owning specifications, validation and sprint trackers determine delivery status. The shared overview and board are derived views. Work the first incomplete required epic in dependency order. Later-release work stays visible and is never marked done to close a release.
