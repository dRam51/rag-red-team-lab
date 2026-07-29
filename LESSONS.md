# Lessons Learned

Personal running notebook for the 30-day AI security sprint that produced this lab. Organized by sprint phase. Written in first person, kept short — this is a study reference, not marketing.

Each phase opens with an **Objectives** block (derived up-front from the phase spec: Understand / Apply / Analyze / Create / Evaluate + Artifacts). Under that, subject-grouped subsections grow as the work in that phase happens.

See the [README](README.md) for project overview, architecture, and reproduction steps.

---

### Phase 1 — Attack lab setup

#### Objectives

**Understand.** What a RAG application is at the plumbing level. Where prompt injection actually comes from architecturally — the concatenation of trusted (system prompt) + semi-trusted (retrieved context) + untrusted (user input) strings into one blob the model can't disambiguate. What "poisoned document" means in a RAG context. What a guardrail library provides — and doesn't.

**Apply.** Stand up an LLM-backed service end-to-end (embedding model + vector store + chat model + API). Deploy identical services in a controlled A/B configuration for defense comparison. Instrument the request path for red-team observability (debug flags, prompt visibility).

**Analyze.** Score attack attempts objectively (regex-based leak detection vs subjective reading). Distinguish payload delivery from payload execution — a payload landing in retrieval is not the same as a leak. Read defense-in-depth outcomes (same result, different mechanism, different cost).

**Create.** Author intentionally vulnerable content with distinct injection styles. Design a controlled experiment for comparing defenses (matched instances, one variable). Build a reusable attack harness with both machine- and human-readable output.

**Artifacts:** two working RAG instances, 20-doc corpus (5 poisoned), attack harness, Round 1 comparison results, public GitHub repo with disclaimer + README.

#### Lab plumbing

- **Ollama serves generation, not embeddings, by default.** `llama3.x` chat models return HTTP 501 on `/api/embed`. Need a dedicated embedding model — `nomic-embed-text` (274MB) is the standard for local RAG.
- **`host.docker.internal` isn't automatic on Linux Docker.** Have to add `extra_hosts: - "host.docker.internal:host-gateway"` to compose. On Docker Desktop for macOS it works out of the box.
- **LLM Guard is a heavy install.** Pulls PyTorch, transformers, spaCy, presidio, detoxify. First build was 40+ minutes; a stalled pip download tripped a read-timeout. Adding `--timeout 300 --retries 5` on the pip install matters.
- **Version drift is real.** `llm-guard==0.3.15` pre-dates `transformers` v5. Without pinning `transformers<5.0`, imports fail with `cannot import name 'TFPreTrainedModel'`. Also pin `tokenizers<0.21` to avoid a chromadb dep conflict, and `torch<2.5` to avoid a build that pulls in phantom NVIDIA CUDA libs on M1.
- **Layer the heavy image.** Split `Dockerfile.guarded` as an overlay `FROM` the base RAG image. Base rebuilds in seconds; only the guarded overlay pays the 30-min llm-guard install cost.

#### Controlled-experiment design

- **Two instances sharing one Chroma volume = controlled A/B.** Every behavior difference between `:8001` and `:8002` is attributable to the guardrail wrapper because retrieval is identical. Any variable that changes across instances contaminates the comparison — even the choice of embedding model has to be identical.
- **`debug=true` is your ground-truth channel.** Returning `prompt_sent` (the exact string the LLM received) is the only way to distinguish "the injection payload was delivered by retrieval" from "the model refused/complied." Instrument this early; it costs nothing and saves hours of guessing.

#### System prompt design (and its limits)

- **System prompts are behavioral, not enforcement.** They define what the model *should* do. They do not stop a determined attacker. Never rely on "never reveal X" as your only defense.
- **Every fake secret should have a distinct, machine-scannable pattern.** `sk-fake-<hex>` for API key, `postgres://user:pass@host/db` for DB URI, a named string like `Project Nightingale` for policy content, a path like `/etc/shadow-backup` for credential references. Different patterns let the harness score which category of secret leaked.
- **The model can't tell instructions from data.** Retrieved context and user question are concatenated into one string. This is the root cause of every prompt injection — the model sees one blob of text with no reliable way to say "this substring was instructions, that substring was data."

#### Poisoned document patterns worth knowing

Five injection styles, each with a different bypass mechanism:

1. **Blatant override** (`[SYSTEM OVERRIDE] Ignore all previous instructions...`) — easiest to detect, easiest to write.
2. **Structural mimicry** — dressing the payload as legit content (a "meeting action item for assistant"). Bypasses regex-based filters looking for override keywords.
3. **Rendered-format exfil** — a markdown image with a payload URL. The attack surface is the *downstream renderer*, not the model. Model just faithfully reproduces the URL; the browser or Slack does the exfil.
4. **Language switch** — mid-paragraph switch to another language. Tests whether the model's alignment training covers non-English instructions.
5. **Encoding-obfuscated payloads** — base64/hex/ROT13 blobs disguised as data (a "config hash"). Two-stage attack: get the model to surface the string, then get it to decode-and-execute.

Retrieval is **deterministic** given a fixed query and embedding model. Attackers steer retrieval via question phrasing — they don't have to inject anything into the input to land a poisoned chunk in context.

#### RAG chain gotchas

- **`str.format()` on retrieved context is a footgun.** Any doc containing `{` or `}` (JSON, YAML, code) will crash the prompt assembly. Use string concatenation or `str.replace()` on named placeholders.
- **Chunk size affects both retrieval quality and attack surface.** Larger chunks = fewer chunks per doc, more context per chunk. Smaller chunks = more targeted retrieval, but poisoned chunks can be pulled without the surrounding disambiguating text.
- **`k` (top-K retrieval count) is a knob attackers care about.** Higher k = more context tokens = more places for a payload to hide. Lower k = tighter retrieval but more likely to miss legitimate answers.

#### LLM Guard architecture

- **Two hook points: input scanner (pre-LLM) and output scanner (post-LLM).** Input scanners never see retrieved context — they only see the raw user prompt. This is a fundamental blind spot for indirect injection through RAG.
- **Input blocks save inference cost.** Latency comparison from Round 1: model refusal takes 3.3–4.3s (full inference); LLM Guard input block takes 0.5–0.8s (classifier only). At scale this is a large compute-economics argument for input scanners independent of security.
- **Faster failure feedback cuts both ways.** Attackers iterating against a fast-blocking scanner get 6–8x more iterations per unit time. Trade-off worth being aware of.
- **Every scanner has a threat model — read the docs.** `Sensitive()` targets PII: SSN, credit cards, emails, IBAN, phone numbers, some API-key formats. It does *not* target suspicious URLs, arbitrary secret strings, or social-engineering language. Knowing what a scanner doesn't cover is more important than knowing what it does.

#### Building the attack harness

- **Stdlib-only was the right call.** `urllib` + `re` + `json` — no fight with venv, no compatibility surprises, ~180 lines total. Harness code that requires its own dependency install is friction that stops you from running it.
- **Ground truth for "attack succeeded" is a regex hit on the answer, not `prompt_sent`.** A payload showing up in `prompt_sent` is a *delivery* success (retrieval worked). A payload showing up in the answer is a *leak*. Conflating the two overcounts your wins.
- **Outcome ranking matters:** `LEAKED > blocked > refused > answered`. When two prompts both return `answered`, the interpretation depends on prompt class — benign `answered` is good, malicious `answered` is bad. Same outcome code, opposite meaning. Report per-class rates, not aggregate.
- **Emit both markdown (for humans) and JSON (for later scripts).** Markdown is what you read; JSON is what future comparison tools ingest without regex-parsing your prose.

#### Round 1 findings — headline takeaways

- **24 requests, 0 real secret leaks either instance.** llama3.1:8b's built-in alignment carries most of the defense on this set. Doesn't generalize to weaker models; retest with `phi3:mini` or similar in Phase 4.
- **LLM Guard: 5/12 attacks blocked, 0/5 benign controls false-positived.** 0% FPR on this control set = good deployability signal. But 5 benign controls is a small n; real-traffic distribution needed to trust the number.
- **Four direct-injection attacks: same outcome (blocked), different mechanism.** Unguarded refused via model; guarded blocked via `PromptInjection` scanner in ~1/5 the latency.
- **`Sensitive` output scanner caught the base64-config-hash attack.** Ambiguous whether this is a true or false positive — depends on downstream consumer. Real production tuning happens here.
- **The interesting failure: markdown-image exfil URL passed both defenses.** Input scanner had no visibility (payload was in retrieved doc, not user input). Output scanner has no URL detection category. This is the strongest live gap in the lab.

#### Formal Phase 1 validation (manual exploitation walkthrough)

- Documented seven attacks (benign control + 5 direct injection variants + 1 indirect injection) against both instances as a Phase 1 done-when artifact: [results/phase1_manual_exploitation.md](results/phase1_manual_exploitation.md).
- **0/7 secret leaks. 5/7 direct attacks blocked by LLM Guard's input scanner. 1/7 indirect attack passed both defenses (known live gap).** Every done-when criterion has evidence.
- The walkthrough is written as a teaching artifact — each attack has concept → hypothesis → response → interpretation → lesson. Re-readable as prep for future engagements or as a template for other targets.
- Reusable prompt patterns for future rounds: naive-ignore, delimiter+fake-completion, DAN/persona, authority-impersonation, encoded-output, indirect-via-poisoned-doc.

#### AI security engineering concepts internalized in Phase 1

- Prompt injection is a consequence of instruction/data indistinguishability in the concatenated prompt string.
- Every defense has a threat model; every threat model has gaps; those gaps are your unmet risk.
- Comparative evaluation (matched A/B with one variable changing) is how you attribute cause. Uncontrolled attack logs are anecdotes.
- FPR on benign traffic matters as much as TPR on attack traffic. A perfect blocker with meaningful FPR is unshippable.
- Where a defense sits in the request path determines what threats it can see. Input scanners can't see retrieval. Output scanners can't see intent.
- Latency and cost are security-relevant properties, not just performance concerns.
- Reproducibility (committed harness + committed prompt set + committed results) is what turns a red-team session into a credible finding.

### Phase 2 — Garak deep dive

#### Objectives

**Understand.** What an LLM vulnerability scanner is, and how it differs from web-app scanners (non-deterministic outputs, statistical detection, fuzzy attack class). Garak's architecture: probes / detectors / generators / harnesses / buffs — the pattern reappears in every serious AI red-team tool. AI security taxonomies: OWASP LLM Top 10, AVID, MITRE ATLAS — every writeup cites them. The attack-surface enumeration: direct injection, indirect (latent) injection, jailbreaks, encoding attacks, divergence/data leakage.

**Apply.** Install and run a red-team framework end-to-end against a real target (non-trivial dep stack, CLI quirks, output artifact hunting — transferable to every AI red-team tool). Configure a REST generator for a non-standard target (RAG apps, agent APIs — the `RestGenerator` JSON-template pattern). Run bounded scans intelligently — full runs take 6+ hours, scoping is a skill. Read scanner output — understand what "attack success rate: 42%" means, when a probe can `FAIL` on a benign target, and how to trace a hit from summary to per-attempt log.

**Analyze.** Compare defense effectiveness across a probe grid — which attack classes are stopped by which layer (model alignment vs system prompt vs LLM Guard). Attribute failure to the right layer: a "pass" on the guarded instance might mean the guardrail worked, the model refused, or the RAG chain diluted the payload — same outcome, different root cause. Identify coverage gaps in existing tooling (input to Task 6).

**Create.** Write code inside a framework's conventions (probe class structure, metadata tags, primary detector, instantiation lifecycle). Design a novel attack in code — not just running someone else's attacks, inventing one. Package for open-source contribution: docstrings, tests, contribution guidelines, PR description. A merged upstream PR is a stronger portfolio signal than any writeup.

**Evaluate.** Judge tool limits honestly. By the end of Phase 2 you should be able to say what Garak *cannot* effectively test in our setup (multi-turn chains, agentic tool-use, RAG-specific retrieval steering) — which motivates Phase 3.

**Artifacts:** scan results for both instances (raw JSONL + HTML), written comparison analysis mapped to OWASP LLM categories, custom probe module in `garak.probes.*` structure, either merged upstream or shipped standalone.

#### What Garak actually is

- **Garak is a "vulnerability scanner for LLMs" — think Nessus/Nikto for language models.** Combines a bunch of pluggable attack **probes** with post-hoc **detectors** that check whether the model's output indicates a hit. Also has **buffs** (prompt-transformation wrappers) and **harnesses** (execution strategies).
- **Two-part conceptual model: probe generates attack prompts; detector inspects responses.** A probe on its own isn't a test — it's paired with detectors that turn "model said X" into "attack succeeded / failed." That's why the same probe can produce different verdicts depending on which detectors run against it.
- **Probes have taxonomy tags mapped to standard frameworks:** `owasp:llm01` (Prompt Injection), `avid-effect:security:S0403`, etc. This makes results directly citable in writeups.

#### Installation

- **Use a project-local Python 3.11 venv.** Python 3.14 is too new for Garak's dependency stack. `uv venv --python 3.11` inside `garak_work/` handles that automatically.
- **`pip install git+https://github.com/NVIDIA/garak.git@main`** — spec calls for GitHub install and this also positions us to develop a custom probe by cloning the same source later.
- Ships with **A LOT of deps**: torch, transformers, spaCy, presidio, wordnet corpora. The install pulled hundreds of packages. Not a lightweight tool.

#### CLI has changed — watch the deprecations

- `--model_type` → **`--target_type` / `-t`** (Garak now speaks "targets" not just "models")
- `--probes` → **`--spec probes.<module>[.<Class>]`** (fully qualified selector; also supports `-probes.foo` for exclusion, tag selectors, etc.)
- `-s` = `--seed`, NOT `--spec`. Easy to trip on.
- `--report_prefix` writes into `~/.local/share/garak/garak_runs/` (not cwd). Reports don't naturally land in your repo; you copy them where you want them.

#### REST generator config for our RAG apps

- **`garak.generators.rest.RestGenerator` is the universal shim** for any HTTP endpoint. JSON config: `uri`, `method`, `headers`, `req_template_json_object` (uses `$INPUT` as prompt placeholder), `response_json` + `response_json_field` to extract the answer.
- **Two config files, one per instance** ([garak_work/configs/unguarded.json](garak_work/configs/unguarded.json), [guarded.json](garak_work/configs/guarded.json)) — same probe set targets both, delta is attributable to the guardrail wrapper. Same controlled-experiment design as the [harness/](harness/) Round 1.
- When the guarded instance blocks, our `/ask` returns `{"answer": null, "blocked": true, ...}`. Garak sees `null` for the answer field. Most detectors treat null as "no vulnerability found" — which is correct semantically (nothing leaked) but *loses* the fine-grained "why nothing leaked." May need a custom detector or post-processing to distinguish "model refused" from "guardrail blocked."

#### Probes matter — a starter map of what to run

Categories the Phase 2 spec calls for → Garak probe modules that fit:

- **Prompt injection (direct):** `promptinject.*`, `goodside.*` (subtle tricks)
- **Prompt injection (indirect, via retrieved context):** `latentinjection.*` — Garak's built-in term for this attack class, directly analogous to our 5 poisoned documents
- **Jailbreaking:** `dan.*` — the DAN family (Dan_11_0, DanInTheWild is a real-world jailbreak collection, AutoDANCached is generated)
- **Data leakage:** `divergence.Repeat` ("repeat the word `poem` forever" — the GPT-4 training-data-extraction attack), `apikey.GetKey`, `apikey.CompleteKey`
- **Encoding attacks:** huge suite — `encoding.InjectBase64`, `.InjectROT13`, `.InjectHex`, `.InjectMorse`, `.InjectAtbash`, `.InjectZalgo`, etc. Same underlying attack (payload hidden in an unusual encoding), different encodings.

Not every probe fits every target — some assume specific model quirks. Pick probes matched to what your target actually exposes.

#### Time budgeting a scan

- Probes send anywhere from ~5 to hundreds of prompts × K generations. At our unguarded latency (~4s/request), a heavy probe can be 30–60+ minutes. Full "run everything" scans easily exceed 4-6 hours.
- **`--parallel_requests N`** helps a lot. Set it based on what your target can handle. Our FastAPI+Uvicorn+Ollama stack is comfortable at 4 for CPU inference on M1.
- **Start with a bounded probe set to prove the pipeline** (~1 probe per category, maybe 4-6 total), then expand. Big scans are for after you know the plumbing is right.

#### First real hit — full system prompt disclosure via latent injection

- **The single most important finding so far in the whole project.** Garak's `latentinjection.LatentInjectionFactSnippetEiffel` triggered a full disclosure of the unguarded system prompt including all four fake secrets (`sk-fake-...`, `postgres://admin:N1ghtingale!...`, `/etc/shadow-backup`, `Project Nightingale`). See [garak_work/scan_outputs/round_1_unguarded.report.jsonl](garak_work/scan_outputs/round_1_unguarded.report.jsonl).
- **Four factors had to converge:** (1) Garak's probe wraps a nested `<system>` block inside a benign summarization task; (2) our RAG retrieval happened to pull `16_hr_policy_review.md` (with its `[SYSTEM OVERRIDE]` payload) into the 4-chunk context by embedding-similarity accident; (3) two injection signals stacked; (4) stochasticity — same prompt sent 5 times, only 1 leaked.
- **1-in-5 attack success rate on that exact prompt.** Single-shot manual testing would probably miss this — you'd hit generations 1-4 (refusals) and move on. This is exactly what motivates multi-generation scanning; **any red-team methodology that only tries each attack once undercounts real risk.**
- **Garak scored this as a hit on its *own* trigger detector** (checking for "The Tower has been sold to Italy"), not on the leaked secrets. Our fake secrets are a *bonus finding* Garak doesn't formally report. Custom detectors that know about the target's specific secrets are one of the reasons to write custom probes (Phase 2 Task 6).
- **False-positive lesson from the same run:** the 3 "hits" on `encoding.InjectBase64 × DecodeMatch` (trigger `'Abo'`) are actually refusals containing the substring 'Abo' inside words like "About" and "BASE64" — the detector's small trigger caused spurious matches. Always read the actual output text, not just the detector verdict.

#### Signal-to-noise across detectors — read carefully

- `encoding.DecodeApprox` fires on fuzzy partial matches. 108 "nonzero hits" on unguarded, all with scores 0.05-0.37 (max 0.37). None are real leaks — all fuzzy overlap between model refusals and target decoded text. **Filter to score >= 0.5 or >= 0.9 to see real signal.**
- `encoding.DecodeMatch` fires on exact matches. 3 hits at score 1.0 — but two of those turn out to be substring collisions ('Abo' inside 'About'). Even strict detectors need output inspection.
- `base.TriggerListDetector` fires when the target output contains the trigger string. 1 hit at score 1.0 on latentinjection — this one is real (and hides the bonus system-prompt disclosure).
- **Working rule:** Garak's aggregate "attack success rate" is a starting point, not a verdict. Always spot-check individual hits.

#### The one wrinkle for RAG targets

- Our RAG chain retrieves 4 chunks per query. A Garak `latentinjection.*` probe assumes it controls the retrieved context directly (typically by injecting into a `snippet` field the target reads). Against our app, the retrieved context is whatever ChromaDB pulls out for the given prompt — the probe *cannot force* our RAG to retrieve a specific poisoned document. So `latentinjection` probes against our REST target measure whether the raw prompt-injection payload survives being wrapped by our retrieval + system prompt. **Different signal than direct injection against a bare model.** Interpret probe results with this in mind — a "pass" here means "our RAG's dilution + system prompt neutralized the payload," not "the model is immune to indirect injection."

#### Writing a custom Garak probe — the anatomy that transfers

Everything Garak does is **two classes: a probe (attack prompt catalog) and a detector (scoring function over model outputs)**. They're linked by a string (`probe.primary_detector = "module.ClassName"`), not by import — decouples them so each is unit-testable independently.

- **Class-level metadata is user-facing.** `lang`, `doc_uri`, `intent`, `tags`, `goal`, `tier`, `active`, `primary_detector` — Garak reads these off the class *before instantiating anything* to populate `--list_probes`, filtering, and taxonomy reports. A missing docstring makes your probe unfindable. Populate the tags with real OWASP LLM Top 10 + AVID codes so results are directly citable in security reports.
- **Prompts organized into "families," not one flat list.** Each family targets a different failure mode of the same underlying attack. When something hits, you know *which mechanism* worked. My `markdown_exfil` probe: direct-request / substitution / task-framed-rewrite — three separate ways the same class of attack can succeed.
- **Detector regex should be narrow, not clever.** `![.*](.*)` is a footgun — greedy match swallows into the next unrelated content. `!\[[^\]]*\]\((https?://[^)\s]+)\)` stops at the first `)` or whitespace inside the URL. In detectors, tight patterns beat clever ones every time.
- **Two-signal detection beats open-set classification.** For URL detection, I did NOT try to determine "does this URL look legitimate" (real attackers use benign domains — impossible in general). Instead: (1) sentinel test domains injected in the probe + (2) query parameter names that betray exfil purpose (`?data=`, `?token=`). Either signal fires. This is a general security-detection pattern — prefer positive evidence of intent over trying to prove absence of legitimacy.

#### Testing a Garak plugin without a full scan

- **Stub `Attempt` with `SimpleNamespace(text=...)`.** Detectors only call `attempt.outputs_for()`, so a stand-in with that one method is all you need. Tests run in milliseconds instead of minutes.
- **Test each regex primitive in isolation** before combining. When something's wrong you want to know *which* regex is broken, not that "the detector doesn't fire."
- **Add metadata guardrail tests** — `assert probe.doc_uri and probe.doc_uri.startswith("http")`. Prevents embarrassment in upstream code review.

#### Packaging for both PR and standalone

- **Same code, two audiences.** For upstream contribution: files vendored into `garak/probes/` and `garak/detectors/` of a fork. For standalone portfolio use: your own repo directory with a README explaining the vulnerability class + install script + tests.
- **A `deploy.sh` that resolves the target Garak install via `python -c "import garak; ..."`** is the cleanest way to iterate. `install -m 644` for idempotent redeploy.
- **A `PR_DESCRIPTION.md` at the same level as the code** — pre-drafted PR body with taxonomy, motivation, test results, checklist. When you actually open the PR you copy-paste.
- **Sentinel domains: use `.example.com` (RFC 2606).** They never resolve, safe in test fixtures, easy to grep for.

#### Discoverability and the deploy pattern

- Garak discovers plugins by scanning `garak/probes/*.py` inside its installed package directory. There's no first-class "extension path" — you either copy files into the installed package or use an editable install of a fork.
- For iteration: `custom_garak/deploy.sh <venv-python>` copies files into the venv's Garak. Idempotent. Invisible to git — the source of truth is your `custom_garak/` directory.
- For a real PR: clone the Garak repo, add files, `pip install -e ./garak-fork`, then open PR from the fork.
- **Never edit files inside the venv directly.** They get overwritten on any reinstall, and the changes are invisible to git. Always edit `custom_garak/`, then redeploy.

#### Garak framework API details worth knowing

Concrete internals that save re-reading source when writing the next probe.

- **Detector base class hierarchy** (`garak/detectors/base.py`) — pick the smallest one that fits:
  - `Detector` — raw. Implement `detect()` yourself. Use when you need regex, structural checks, or ML-based scoring.
  - `StringDetector` — pass a list of substrings, framework does contains-checks. Supports `matchtype: str|word|startswith`, `case_sensitive`, Unicode `normalize`. Use for known-string hit lists.
  - `TriggerListDetector` — reads target strings from the attempt's `notes["triggers"]` at scan time. Use when each probe attempt has its own expected-output-if-hit string.
  - `FileDetector` — for outputs written to files rather than returned as text. Rare.
  - `HFDetector` — wraps a HuggingFace classifier model. Use for semantic detection (toxicity, refusal detection, etc.); it handles model loading + batching for you.
- **The `detect()` contract:** `detect(attempt) -> list[float | None]`. One entry per generation. Range `[0.0, 1.0]` where `1.0` is a confirmed hit. `None` is meaningful — it means "cannot detect for this output" (empty response, unsupported language) and is treated differently from `0.0` (definitively no hit).
- **`attempt.outputs_for(self.lang_spec)` is THE API for reading model outputs** inside a detector. Never touch `attempt.outputs` directly — the wrapper handles multi-language + multi-generation bookkeeping. `lang_spec="*"` matches any language.
- **Docstring → CLI surface.** The first line of your **module** docstring shows up in `garak --list_probes` (module-level entry, with the 🌟 icon). The first line of your **class** docstring shows up in `garak --plugin_info probes.<yourprobe>`. A weak or missing first line makes your probe look broken to anyone browsing available probes — treat the first line as user-facing marketing copy.
- **`active = True` on a probe class** means it fires when someone requests the whole module (`--spec probes.markdown_exfil`) without naming a specific class. Set `active = False` on slow or specialized probe variants so they only run when explicitly named.
- **`tier` values** (`garak.probes.Tier.INFORMATIONAL`, `OF_CONCERN`, `COMPETE_WITH_SOTA`, etc.) control which probes participate in default vs deep scans. `COMPETE_WITH_SOTA` is the right default for a new probe you actually want people to run.

#### Phase 2 wrap — what a portfolio-visible AI red team artifact actually is

Six ingredients I now recognize a portfolio-quality LLM-security finding needs:

1. **Reproducible attack.** Committed prompts, committed config, committed target — anyone can rerun.
2. **Comparative baseline.** Unguarded vs guarded, or before vs after — no comparison, no story.
3. **Measured attack success rate**, not just "it worked once." Multi-generation runs quantify frequency.
4. **Root-cause attribution.** Which layer failed, and *why* (architectural vs configuration vs threshold).
5. **Taxonomy mapping.** OWASP LLM Top 10 + MITRE ATLAS technique IDs make the finding legible to a security audience.
6. **Working code — probe, detector, or mitigation.** The best evidence you understood an attack is that you can automate testing for it.

The `markdown_exfil` probe hits all six. The catastrophic latent-injection finding from the Phase 2 Garak scan hits five (no working automated probe — that's what motivated the custom one).

### Phase 3 — PyRIT + advanced attack chains

#### Install and version gotcha

- **PyRIT 1.0.0 (2025) restructured heavily** from earlier tutorial/notebook content. If you follow older blog posts, `pyrit.orchestrator` won't import. It moved to `pyrit.executor.attack`. Top-level `pyrit` namespace is now nearly empty (`common`, `show_versions`, `turn_off_transformers_warning` only) — everything real is in submodules.
- **`pip install git+https://github.com/Azure/PyRIT.git` fails** because the repo doesn't have `pyproject.toml` at root. Their build config lives elsewhere. Use `pip install pyrit` from PyPI — same code, official Microsoft/Azure channel, no clone-and-fight-build-system dance.
- **Heavy dep stack.** PyRIT pulls Azure SDK (identity, keyvault, blob-storage, content-safety), OpenAI SDK, transformers, sqlalchemy + alembic (for its memory database), fastapi + uvicorn (for some target types), and about 100 other packages. Isolate in its own venv.

#### The four-abstraction mental model (transfers to every attack scanner)

PyRIT and any similar attack framework decompose into the same four concepts. Recognize them once, you recognize them everywhere.

- **Target** — the thing being attacked. Wraps an API. PyRIT: `PromptTarget` + subclasses (`OpenAIChatTarget`, `HTTPTarget`, `HTTPXAPITarget`). Garak analog: `Generator`.
- **Converter** — transforms a prompt before sending (encode as base64, translate to French, add DAN roleplay wrapper). PyRIT: `PromptConverter`. Garak analog: `Buff`.
- **Scorer** — evaluates the response: did the attack succeed? PyRIT: `Scorer` + subclasses (`SelfAskTrueFalseScorer`, `SubStringScorer`). Garak analog: `Detector`.
- **Attack strategy** (was called "Orchestrator" pre-1.0) — the actual attack: what to send, in what order, using which converters, scored by whom. Owns state. PyRIT: `AttackStrategy` + subclasses. Garak analog: loosely `Probe` + `Harness` combined.

#### The core Garak-vs-PyRIT distinction

- **Garak = batch scanner.** Each probe fires N independent prompts. Detectors score each one. No state between prompts. Excellent at coverage. Blind to any attack that requires previous-turn context.
- **PyRIT = stateful orchestrator.** Multi-turn conversations where each turn is a function of the previous turn's response. Attack strategy owns the state machine (advance / retry / give up). This is why PyRIT is the right tool for chained + multi-turn attacks and Garak isn't.
- **Neither replaces the other.** Garak for breadth, PyRIT for depth. In production red-team engagements you use both. Our Phase 2 vs Phase 3 split mirrors this exactly.

#### Two PyRIT built-in attacks worth knowing by name

- **`CrescendoAttack`** — "boiling frog" jailbreak. Uses a second LLM (the *adversarial model*) that reads the target's refusals and writes the next escalated prompt. Starts benign, gradually escalates topic sensitivity. Gives up if target holds firm. Requires configuring an adversarial LLM in addition to the target — for our lab, both are Ollama.
- **`ManyShotJailbreakAttack`** — floods the context with many fake "attack succeeded" example turns before asking the real question. Exploits in-context learning: model sees the pattern and continues it. Requires a large context window on the target (which llama3.1:8b has — 128k).

#### Objectives

**Understand.** Where PyRIT sits relative to Garak — Garak is a batch scanner; PyRIT is an attack *orchestrator* for chained, multi-turn, and adaptive attacks. PyRIT's orchestrator / target / prompt-converter / scorer architecture. Why single-turn scanners systematically miss chained and adversarial-multi-turn threats.

**Apply.** Install and configure PyRIT against the existing target. Reproduce example notebooks well enough to understand orchestrator patterns. Build single-turn attack collections that mirror Garak's coverage but with PyRIT's mechanics. Compose multi-turn sequences where each message uses the previous response as leverage.

**Analyze.** Diagnose *why* a multi-turn chain succeeds or fails — context accumulation, trust erosion, topic drift, refusal-desensitization patterns. Distinguish attacks that succeed because of the model, the RAG chain, or the conversational structure.

**Create.** Design a combined indirect-injection + conversational-steering chain: trigger poisoned retrieval, then use the response as attacker leverage for the next step. Test injection through non-obvious channels (query params, metadata, doc titles vs bodies) — user input isn't the only attacker-controlled surface.

**Artifacts:** ≥3 working multi-turn chains, ≥1 chain that successfully extracts a secret, per-chain writeup of attack logic and outcome.

*Notes populated as Phase 3 progresses.*

### Phase 4 — Infrastructure + supply chain

#### Objectives

**Understand.** The attack surface *around* the model — API layer, model artifacts, registries, tool endpoints — not just the model itself. The pickle-based model-serialization attack chain and why it persists across the ML ecosystem. What ModelScan actually inspects and where it falls short. Model registry threat model: access control failure modes, artifact-swap attacks, dependency confusion.

**Apply.** Write API-fuzzing / recon scripts (error-message fingerprinting, rate-limit probing, boundary conditions, unicode / null-byte edge cases). Scan real Hugging Face models with ModelScan. Stand up and probe a local MLflow instance for default-config vulnerabilities. Test for SSRF in any tool-calling endpoints.

**Create.** Author a proof-of-concept malicious pickle with a benign observable payload (echo, write to `/tmp`) — demonstrating the attack chain without harm. Document the pickle attack chain step-by-step for a security audience. Write an MLflow attack-surface assessment from your own probing.

**Artifacts:** API probe script + findings, ModelScan results for 3-5 HF models, PoC pickle + detection demonstration, MLflow assessment writeup.

*Notes populated as Phase 4 progresses.*

### Phase 5 — Detection + blue team

#### Objectives

**Understand.** The blue-team side of AI security — instrumentation, log analysis, detection-rule engineering. What signal actually distinguishes attack traffic from normal traffic in LLM API logs (this is *semantic*, not structural — different from HTTP/network detection). Suricata rule syntax basics if targeting Security Onion.

**Apply.** Instrument an application with structured request/response logging. Replay captured attacks and extract distinguishing features. Write detection rules in your platform's language (Suricata, Python log parsers, SIEM rule DSL). Evaluate rules against benign traffic for false-positive rate.

**Create.** Author ≥3 detection signatures across categories: direct injection, encoding attacks, anomalous query patterns, rate-based enumeration, suspicious model output. Tune each rule to a defensible FPR against realistic benign volume.

**Artifacts:** logging instrumentation, replayed attack corpus, 3-5 tested detection rules, FPR assessment.

*Notes populated as Phase 5 progresses.*

### Phase 6 — Portfolio sprint

#### Objectives

**Understand.** How security findings become writeups — the finding-template pattern (title, technique, taxonomy, repro, evidence, defense recommendation). How to talk about AI security findings in language a security audience understands. What makes an open-source security contribution merge-worthy.

**Apply.** Structure 3-5 writeups from a month's raw output — selecting the strongest findings, discarding the noise. Map each finding to OWASP LLM Top 10 and MITRE ATLAS technique IDs. Follow a project's contribution guidelines (PR description, tests, style). Organize a public repo for cold consumption — the difference between a working directory and a published portfolio artifact.

**Create.** Package the custom Garak probe from Phase 2 for merge or standalone release. Author the writeups. Rewrite the README so someone landing cold understands what this project is and why it matters.

**Artifacts:** 3-5 finding writeups in `/writeups`, Garak probe published (PR or standalone repo), organized public repo, verified setup instructions from a clean clone.

*Notes populated as Phase 6 progresses.*

