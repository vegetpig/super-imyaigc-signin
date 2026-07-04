---
name: 浣跨敤IMYAI
description: "浣跨敤IMYAI. Let Codex App call IMYAI official chat and image models by human-readable model name while Codex App remains the orchestrator and tool executor. Use when the user says 浣跨敤IMYAI, asks Codex App to use an IMYAI model for later replies, names an IMYAI model such as Claude Opus/Sonnet, Qwen, Gemini, Ava, GPT Image, Nano Banana, or asks for IMYAI image generation, login, JWT verification, model discovery, session mode, or IMYAI-first repair."
---

# 浣跨敤IMYAI

This skill is for Codex App plus IMYAI. Codex App remains the active assistant, planner, tool caller, file editor, command runner, and test executor. IMYAI is called through the bundled scripts for official model chat output or official image generation output.

Do not present this as Codex CLI provider integration. Do not change `C:\Users\18511\.codex\config.toml` or switch Codex App model providers unless the user explicitly asks for that separate provider setup.

## Login and session

Run `scripts/signin.py` when cookies are missing, expired, or the user asks to log in/check JWT.

`signin.py` also supports daily check-in for every account in `scripts/config.json`. Run without `--phone` or `--account` to process all configured accounts. After each check-in it saves a post-check-in screenshot, confirms the current day is signed in, and prints the current streak as `Consecutive sign-in days: X` when the IMYAI page exposes `已连续签到X天`.

For scheduled automations, use `--skip-success-today`. This stores today's successful account state in the configured cookie directory. Later runs on the same day print `OK SKIPPED streakDays=X` for accounts that already succeeded, without opening the browser again. Accounts that have not succeeded today are still processed normally.

Network behavior is automatic. The scripts try the configured proxy first when `config.json` has `"proxy.enabled": true`, then direct connection, then environment proxies, then detected local Clash/Mihomo HTTP or mixed ports from common config files. If no proxy is configured or available, they stay direct. Set `IMYAI_NETWORK_DEBUG=1` before a command to print which route is being tried.

Playwright login behavior: `signin.py` uses a direct browser connection by default. It uses a proxy only when `config.json` has `"proxy.enabled": true`; set `"proxy.auto_detect": true` only when the local Clash/Mihomo mixed port is known to work as an HTTP proxy. This avoids accidentally treating unrelated open localhost ports as browser proxies.

If API calls return 401 and automatic login refresh fails, retry with a visible browser:

```bash
python "C:\Users\18511\.codex\skills\super-imyaigc-signin\scripts\signin.py" --phone YOUR_PHONE --no-headless --login-only
```

```bash
python "C:\Users\18511\.codex\skills\super-imyaigc-signin\scripts\signin.py" --phone YOUR_PHONE --password <PASSWORD> --login-only
python "C:\Users\18511\.codex\skills\super-imyaigc-signin\scripts\signin.py" --phone YOUR_PHONE --password <PASSWORD> --model-count
python "C:\Users\18511\.codex\skills\super-imyaigc-signin\scripts\signin.py" --retries 1
python "C:\Users\18511\.codex\skills\super-imyaigc-signin\scripts\signin.py" --retries 1 --no-cleanup --skip-success-today
```

Saved cookies live in the `cookie_dir` configured by `scripts/config.json`.

`scripts/imyai_proxy.py` automatically refreshes login once with `signin.py --login-only` when an official API call returns 401/UNAUTHORIZED/login-expired. If the retry still fails, run `signin.py --login-only` for the selected phone and then retry the original command.

## Ask an IMYAI model

Run `scripts/imyai_chat.py` when the user wants Codex App to get output from an official IMYAI chat model. Select models by human-readable names first, feed the user's prompt to the selected model, capture the returned text, and then let Codex App decide whether to return it directly or use tools/files/tests around it.

When the user explicitly asks an IMYAI model to help complete a task, do not use `--no-official-history` by default. Use the official group/history path so the IMYAI model requests and replies are visible in the IMYAI website conversation. This includes later repair prompts, adjustment prompts, design revisions, and code-generation follow-ups for the same task.

Use `--no-official-history` only when the message is not meant for IMYAI task work, for example: pure Codex App coordination with the user, local file inspection, command execution, screenshot/render checks, syntax/test runs, status explanations, or when the user explicitly says not to record/send the IMYAI content. Codex App tool activity itself never goes to IMYAI unless Codex App deliberately summarizes it and sends that summary as an IMYAI prompt.

## IMYAI session mode in Codex App

When the user asks to use IMYAI as the main chat model for later messages, use `--session auto`. This stores the selected model, official group id, and recent local conversation history for the current Codex workspace.

```bash
python "C:\Users\18511\.codex\skills\super-imyaigc-signin\scripts\imyai_chat.py" --phone YOUR_PHONE --session auto --set-session-model "Qwen 3.6 flash" --json
python "C:\Users\18511\.codex\skills\super-imyaigc-signin\scripts\imyai_chat.py" --phone YOUR_PHONE --session auto --prompt "Remember the codeword: bridge-726. Reply only: ok" --json
python "C:\Users\18511\.codex\skills\super-imyaigc-signin\scripts\imyai_chat.py" --phone YOUR_PHONE --session auto --prompt "What codeword did I ask you to remember?" --json
```

Codex App orchestration rules:

1. If the user says `浣跨敤IMYAI锛屽悗闈㈤兘鐢?<model name>` or similar, Codex App should first run `--session auto --set-session-model "<model name>"`.
2. For ordinary follow-up messages in that IMYAI mode, Codex App should run `imyai_chat.py --session auto --prompt ... --json` and use the IMYAI `text` as the primary answer.
3. If the user asks for tool work, Codex App should execute the tools itself, then either summarize the tool result directly or pass the relevant tool result back through `imyai_chat.py --session auto --prompt ... --json`.
4. If the user wants to stop or reset IMYAI mode, run `--session auto --clear-session --json`.
5. If the user names an IMYAI model for a specific task but does not ask for ongoing IMYAI chat mode, start or reuse an official group for that task and keep all IMYAI design/code/repair prompts for that task in the same group. Do not send pure Codex App coordination, local command output, screenshots, or status chatter unless it is intentionally summarized as context for the IMYAI model.
6. If the user asks multiple IMYAI models to work on the same task, use one official task group and call each selected model with the same `--group-id`. Codex App assigns roles, compares outputs, applies the chosen result locally, and sends repair/review prompts back to the relevant model in that same group.
7. When IMYAI participates in completing a task, the final Codex App response must report IMYAI point usage with both fields: `鏅€氱Н鍒哷 and `楂樼骇绉垎`. If exact usage is unavailable from the API or balance snapshots, write `鏃犳硶缁熻` for that field rather than estimating.

This does not replace the Codex App model provider. Codex App remains the orchestrator and tool executor; IMYAI is the preferred chat/reasoning source while the session is active.

Default behavior:

- Create or reuse an official IMYAI group.
- Send the prompt with `options.groupId` so the upstream conversation can appear in the official IMYAI chat history.
- Query `chatlog/chatList` after the reply and include the returned history metadata in `--json`.

Common commands:

```bash
python "C:\Users\18511\.codex\skills\super-imyaigc-signin\scripts\imyai_chat.py" --phone YOUR_PHONE --search-model claude
python "C:\Users\18511\.codex\skills\super-imyaigc-signin\scripts\imyai_chat.py" --phone YOUR_PHONE --list-models-compact
python "C:\Users\18511\.codex\skills\super-imyaigc-signin\scripts\imyai_chat.py" --phone YOUR_PHONE --model "Claude Sonnet 4.6" --prompt "Reply with one short paragraph about RAG evaluation." --json
python "C:\Users\18511\.codex\skills\super-imyaigc-signin\scripts\imyai_chat.py" --phone YOUR_PHONE --model "Qwen 3.6 flash" --prompt "Reply exactly: ok" --no-official-history --json
python "C:\Users\18511\.codex\skills\super-imyaigc-signin\scripts\imyai_chat.py" --phone YOUR_PHONE --model "Claude Opus 4.8" --prompt "Reply exactly: ok" --json
python "C:\Users\18511\.codex\skills\super-imyaigc-signin\scripts\imyai_chat.py" --phone YOUR_PHONE --model "Qwen 3.6 flash" --prompt "Reply exactly: ok" --json
python "C:\Users\18511\.codex\skills\super-imyaigc-signin\scripts\imyai_chat.py" --phone YOUR_PHONE --model "Ava" --prompt-file .\work\prompt.txt --json
python "C:\Users\18511\.codex\skills\super-imyaigc-signin\scripts\imyai_chat.py" --phone YOUR_PHONE --model "Claude Sonnet 4.6" --group-id 1211383 --prompt "Continue the existing official conversation." --json
python "C:\Users\18511\.codex\skills\super-imyaigc-signin\scripts\imyai_chat.py" --phone YOUR_PHONE --session auto --prompt "Continue the active Codex App IMYAI session." --json
```

Multi-model task example:

```bash
python "C:\Users\18511\.codex\skills\super-imyaigc-signin\scripts\imyai_chat.py" --phone YOUR_PHONE --model "Gemini-3.5-flash" --prompt "Role: UI designer. Propose the design direction for this task." --json
python "C:\Users\18511\.codex\skills\super-imyaigc-signin\scripts\imyai_chat.py" --phone YOUR_PHONE --model "Claude Opus 4.7" --group-id <groupId from previous JSON official.groupId> --prompt "Role: senior engineer. Implement or critique the chosen design for the same task." --json
python "C:\Users\18511\.codex\skills\super-imyaigc-signin\scripts\imyai_chat.py" --phone YOUR_PHONE --model "Qwen 3.6 flash" --group-id <same groupId> --prompt "Role: reviewer. Check the proposed patch for bugs and edge cases." --json
```

Model selection:

- Use `--list-models-compact` to show enabled official model names first; internal ids stay hidden from the compact user-facing table.
- Use `--search-model QUERY` before asking if the user names a model family or variant.
- Use the displayed model name with `--model`, e.g. `Claude-Sonnet-4.6`, `Claude Sonnet 4.6`, `Qwen 3.6 flash`, `Gemini 3.1 pro`, or `Ava`.
- Do not ask the user to choose internal ids such as `imyai-16`; use those only internally or in JSON debugging when name matching is ambiguous or broken.
- Use convenience family names like `claude`, `opus`, `sonnet`, `haiku`, `qwen`, or `ava` only when the user asks for a family without an exact model.
- Use `--no-official-history` only for transient health checks or Codex-only calls that should not be tied to an IMYAI task record.
- Use `--group-id` when continuing a prior official IMYAI conversation.
- If IMYAI is actively helping complete the task, reuse the same `--group-id` for follow-up modification/repair prompts so the IMYAI website shows one coherent task conversation.
- For multi-model tasks, do not create a new group per model unless the user explicitly wants separate conversations. The website group may show the most recent model as the group metadata, but the task history stays together.
- JSON output is compact by default. Use `--include-official-history` only when debugging raw official group/chat history payloads.

Point usage reporting:

- Every final answer after IMYAI-assisted task work must include `鏅€氱Н鍒嗭細...` and `楂樼骇绉垎锛?..`.
- If a reliable point balance/usage API is available, record balances before and after the IMYAI task and report the difference.
- If the response payload or balance API does not expose point usage, report `鏅€氱Н鍒嗭細鏃犳硶缁熻` and/or `楂樼骇绉垎锛氭棤娉曠粺璁.
- Do not infer point usage from token counts, model names, or number of calls unless an official IMYAI API explicitly defines that conversion.
- Pure Codex App work that did not call IMYAI does not need IMYAI point usage reporting.

## IMYAI image generation in Codex App

Run `scripts/imyai_image.py` when the user asks Codex App to generate an image using IMYAI. Codex App should write or refine the prompt, use automatic model selection unless the user names a specific drawing model, submit through the official encrypted `/draw/runtime-models/{versionId}/invoke` API, poll `/draw/mineList`, and download the final image to the workspace output folder.

IMYAI draw prompts must stay within the official 1000-character limit. Before calling `imyai_image.py`, count the final prompt after any additions such as text-legibility guardrails; if it may exceed 1000 characters, compress it first. Prefer one compact paragraph or 5-8 short clauses covering subject, scene, style, composition, palette, exact text, and negative constraints. Do not send long prompt schemas, copied document excerpts, or verbose rationale to image generation. If exact in-image text is needed, keep the visible text list very short and use `--no-text-guard` when the prompt is already near the limit.

Common commands:

```bash
python "C:\Users\18511\.codex\skills\super-imyaigc-signin\scripts\imyai_image.py" --phone YOUR_PHONE --list-models-compact
python "C:\Users\18511\.codex\skills\super-imyaigc-signin\scripts\imyai_image.py" --phone YOUR_PHONE --search-model "GPT Image"
python "C:\Users\18511\.codex\skills\super-imyaigc-signin\scripts\imyai_image.py" --phone YOUR_PHONE --model "GPT Image 2" --manifest
python "C:\Users\18511\.codex\skills\super-imyaigc-signin\scripts\imyai_image.py" --phone YOUR_PHONE --model auto --prompt-file .\work\image_prompt.txt --resolution 1K --ratio 9:16 --count 2 --json
python "C:\Users\18511\.codex\skills\super-imyaigc-signin\scripts\imyai_image.py" --phone YOUR_PHONE --model "GPT Image 2" --prompt "A precise product-style prompt written by Codex App." --resolution 1K --ratio 1:1 --json
python "C:\Users\18511\.codex\skills\super-imyaigc-signin\scripts\imyai_image.py" --phone YOUR_PHONE --model "Nano Banana 2" --prompt "Use the uploaded image as style reference." --reference-image "D:\path\to\reference.png" --resolution 1K --ratio 9:16 --json
python "C:\Users\18511\.codex\skills\super-imyaigc-signin\scripts\imyai_image.py" --phone YOUR_PHONE --model "GPT Image 2" --poll-task-id 1307618 --json
python "C:\Users\18511\.codex\skills\super-imyaigc-signin\scripts\imyai_image.py" --phone YOUR_PHONE --model "GPT Image 2" --poll-task-id 1307694 --json
```

`--reference-image` accepts either an HTTP(S) URL or a local `png`, `jpg`, `jpeg`, or `webp` file path. Local files are automatically uploaded through the official IMYAI/COS upload flow, then the returned public URL is sent to the draw runtime input. The JSON result includes `referenceImages` and `uploadedReferenceImages` for debugging.

Image model selection:

- Use `--list-models-compact` or `--search-model QUERY`; show users model/version names, not internal version ids.
- Examples include `GPT Image 2`, `Nano Banana 2`, `Nano Banana Pro`, `Qwen Image 2`, `Seedream 5.0 Lite`, `Wan2.7 Image`, `Kling V3 Omni`, `Niji Journey V7`, and `Midjourney V8.1`.
- Use ids such as `versionId=13` only internally after a name has been resolved.
- Default to `--model auto` when the user does not explicitly choose a model. The script selects a model by prompt type:
  - Text-heavy teaching images, UI diagrams, code screenshots, flowcharts, tables, labels, or Chinese/English text in the image: prefer `Qwen Image 2`, then `GPT Image 2`, then `Nano Banana 2` / `Nano Banana Pro`.
  - Image editing or reference-image tasks: prefer `Nano Banana 2`, then `GPT Image 2`.
  - Photorealistic product, portrait, interior, architecture, or realistic scene tasks: prefer `GPT Image 2`, then `Nano Banana 2`, then `Seedream 5.0 Lite`.
  - Stylized illustration, anime, fantasy, game, or concept-art tasks: prefer `Midjourney V8.1`, `Niji Journey V7`, or `Kling V3 Omni` when available.
- When the user wants options or when quality matters and credits allow it, use `--count 2`. The script submits up to two parallel candidate tasks for models that support practical parallel generation, including `Nano Banana*` and `GPT Image 2`; keep `--count 1` for quick tests or low-credit situations.
- For images containing exact text, code, UI labels, formulas, class names, or API names, never rely on a vague prompt. Keep text short, make it large, ask for "clear editable-looking printed text", and preserve exact strings. The script automatically appends a text-legibility guard unless `--no-text-guard` is passed.
- If generated text is garbled, blurry, pseudo-text, or character-corrupted, treat the image as failed for teaching use. Regenerate with less text, larger font, stronger contrast, and the exact strings repeated in a dedicated "must render exactly" section; prefer `Qwen Image 2` for this repair.
- Prefer low-frequency testing. For verification, reuse `--poll-task-id` for an existing record before submitting a new draw task.
- JSON output is compact by default. Use `--include-poll-response` only when debugging raw `/draw/mineList` payloads, because the full mine list can be very large.

## Codex App coding workflow

When the user asks for an IMYAI model result inside a Codex conversation:

1. Refresh login first only if saved cookies are missing or the official API returns 401.
2. Select the requested model by name with `--model`; if ambiguous, run `--search-model` or `--list-models-compact` and pick a human-readable model name.
3. Send the user's intended prompt through `imyai_chat.py`.
4. Treat the returned text as IMYAI model output.
5. If the task needs file edits, command execution, tests, local inspection, screenshots, or browser/tool calls, Codex App performs those actions itself.
6. If execution or tests fail, Codex App can send the error summary back to the same IMYAI model for repair, then Codex App applies and verifies the repaired output.

For example, if the user says `浣跨敤IMYAI锛岀敤 Claude Opus 4.7 鍐欒繖娈典唬鐮乣, Codex App asks IMYAI's matching Claude Opus model for the code, then Codex App applies the patch, runs tests, and only re-asks IMYAI when the generated code needs repair.

Real end-to-end testing showed the most reliable coding pattern:

1. Ask IMYAI for the smallest useful implementation artifact, e.g. one file, one patch, or one focused function. Avoid asking IMYAI to own the whole project plus all tests unless the user explicitly wants that.
2. Codex App writes or adapts the acceptance tests itself. Do not treat IMYAI's self-written tests as sufficient verification.
3. Codex App writes files as UTF-8, compiles/parses generated code before execution, and runs subprocesses with UTF-8 plus replacement error handling when capturing output.
4. If generated output is not valid code/JSON/patch, retry IMYAI once with a stricter prompt that includes the parse error and says to return only the required artifact.
5. If tests fail, send only the relevant failing output and current file contents back to the same IMYAI model, then Codex App applies the repaired output and reruns tests.
6. If the user has asked an IMYAI model to help complete the task, keep these code-generation and repair calls in official IMYAI history by reusing the same group. Use `--no-official-history` only for Codex-only coordination, local verification, or when the user explicitly does not want IMYAI content recorded.

## IMYAI-first repair loop

If an IMYAI-produced answer, code block, or artifact is wrong or incomplete:

1. Re-run the same IMYAI model first, using the smallest prompt needed to describe the defect.
2. Ask IMYAI to repair its own output before rewriting anything in Codex.
3. Use Codex App only for orchestration, integration, verification, or a final fallback when IMYAI still cannot fix the issue.
4. Keep the repaired IMYAI output as the primary source of truth when updating files or final answers.

## Verification

Verify in this order after edits:

1. `signin.py --model-count` reports supported models.
2. `signin.py --retries 1` processes all configured accounts, returns `OK` for each signed-in account, saves `post-signin-*.png` screenshots, and logs `Consecutive sign-in days: X` when available.
3. A second same-day `signin.py --retries 1 --no-cleanup --skip-success-today` run returns `OK SKIPPED streakDays=X` for accounts that already succeeded without opening the browser.
4. `imyai_chat.py --model "Qwen 3.6 flash" --prompt "Reply exactly: ok" --no-official-history --json` returns non-empty `text`; this catches expired chat JWTs better than model-count alone.
5. `imyai_chat.py --list-models-compact` returns enabled model entries without requiring the user to choose internal ids.
6. `imyai_chat.py --search-model claude` returns Claude model entries.
7. `imyai_chat.py --model "Ava" --prompt "Reply exactly: ok" --json` returns non-empty `text`.
8. `imyai_chat.py --model "Qwen 3.6 flash" --prompt "Reply exactly: ok" --json` returns non-empty `text`.
9. `imyai_chat.py --model "Claude Sonnet 4.6" --prompt "Reply exactly: ok" --json` returns non-empty `text` when that model has sufficient credits.
10. `imyai_chat.py --session auto --set-session-model "Qwen 3.6 flash" --json` saves a session model.
11. Two `imyai_chat.py --session auto --prompt ... --json` calls preserve local context via `historyInjected=true` on the second call.
12. Run at least one real Codex App coding simulation: IMYAI generates implementation code, Codex App writes independent acceptance tests, runs them, and re-asks IMYAI on failure.
13. `imyai_image.py --list-models-compact` returns enabled drawing model entries.
14. `imyai_image.py --model "GPT Image 2" --manifest` returns runtime inputs including `prompt` and `size`.
15. `imyai_image.py --model "GPT Image 2" --poll-task-id <existing record id> --json` finds `/draw/mineList` rows and downloads final images without submitting a new task.
16. For full image verification, submit one low-frequency 1K task, poll to SUCCESS, download the final image, and inspect the saved image file.
17. `quick_validate.py` reports the skill is valid when available.

## Notes

- `scripts/imyai_chat.py` reuses the encrypted official chat request implementation from `scripts/imyai_proxy.py`.
- `scripts/imyai_image.py` reuses the same saved login/JWT and calls IMYAI's official drawing runtime endpoints.
- Any enabled official chat model can be selected by displayed model name, subject to account credits and official availability.
- Some premium variants can return official credit errors; in that case choose another model from `--search-model` or `--list-models-compact`.
- If a request fails with 401, refresh cookies with `signin.py --login-only`.
- Keep Codex App as the orchestrator; IMYAI model output is an input artifact for Codex App to process, execute, verify, or return.
- Official `groupId` records IMYAI chat history, but local session history is what makes follow-up prompts reliably conversational in Codex App.

