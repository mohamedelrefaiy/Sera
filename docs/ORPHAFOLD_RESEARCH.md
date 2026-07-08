# OrphaFold — Deep-Dive Research Report

> Compiled **2026-07-07** for the *Built with Claude: Life Sciences* project (Target Triage).
> Every substantive claim is source-linked. Discrepancies between sources are flagged, not resolved.

**Links**
- **Devpost:** https://devpost.com/software/orphafold
- **GitHub:** https://github.com/Paulhb7/orphafold
- **Live demo:** https://orphafold-deep-structural-search-for-rare-disease-61857687256.us-west1.run.app (Google Cloud Run, us-west1)

**One-line summary:** A solo-built, single-domain (rare-disease) demonstration of Gemini-3 agentic reasoning over public bio databases, with strong structural-biology UX (3Dmol.js AlphaFold visualization). It relies on LLM-generated feasibility scores with no deterministic eval, no MCP, and no validation controls — which is precisely where a Claude + MCP + control-gated target-triage instrument differentiates.

---

## 1. What it is

**Tagline (Devpost):** "OrphaFold orchestrates specialized Gemini 3 agents to bridge Orphanet, UniProt, Pubmed and AlphaFold data. Our AI Agents automates rare disease research to discover hidden therapeutic connections."

**Product description:** An "agentic research platform that automates the discovery phase for orphan diseases" (Devpost). The GitHub README frames it as "an AI-powered platform designed to accelerate research into orphan diseases by combining real-time API enrichment with advanced Multi-Agent orchestration and structural biology."

**The problem it solves — the "cold start" of rare-disease research:**
- 300 million people live with a rare disease; **95% of 7,000+ conditions have no approved treatment**.
- Data is fragmented across disconnected silos: clinical profiles (Orphanet), molecular data (UniProt), structural insights (AlphaFold), and literature (PubMed) don't talk to each other.
- Goal: "democratize drug repurposing by identifying hidden connections between existing drugs and rare proteins through **structural homology analysis**."

**Target users:** Professional researchers and geneticists. Explicitly positioned as "a decision-support tool, not as a clinical system."

**Inspiration:** The creator (Paul) ties his motivation to "developing AI for good" and personal experience with neurodegenerative diseases, plus the data-fragmentation problem above.

*Sources: https://devpost.com/software/orphafold · https://github.com/Paulhb7/orphafold*

---

## 2. Architecture — the multi-agent design

**Four specialized Gemini agents** in a **sequential pipeline** — outputs from earlier agents become grounded context for later ones. A **"Pre-Flight layer"** fetches raw structural data *first*, then feeds it into the model as grounded context to reduce hallucination.

| # | Agent | Responsibility | Data sources | Output |
|---|-------|----------------|--------------|--------|
| 1 | **Clinical Grounding Agent** | Establishes the clinical baseline via direct REST APIs | Orphanet, OMIM (NCBI E-utilities), Google Search Grounding | Prevalence, inheritance patterns, disease classifications |
| 2 | **Bio-Mechanism Agent** | Uncovers molecular pathophysiology & structural machinery | UniProt, NCBI Gene, ClinVar, AlphaFold DB | Target proteins, functional domains, pLDDT confidence, druggability |
| 3 | **Discovery Agent** | Connects the disease to the broader research & clinical landscape | PubMed, ClinicalTrials.gov, structural homology search | Active trials, synthesized bibliography, cross-disease insights |
| 4 | **Drug Repurposing Agent** (a.k.a. "Hypothesis Lab") | Proposes therapeutic candidates by bridging mechanism overlap via structural homology (runs **on-demand**) | DrugBank, ChEMBL, reasoning engine ("thinking budget"), 3D binding-pocket analysis | In-silico repurposing hypotheses with feasibility scores |

**Orchestration notes:**
- Flow is **sequential** ("the gathered context is fed into the Multi-Agent System"); the 4th agent runs **on-demand**, not automatically.
- Devpost describes "multi-layered" reasoning that "cross-validates clinical registries with structural proteomic data."
- **Naming caveat:** Devpost labels the 4th stage "Hypothesis Lab" (structural homology + binding-pocket comparison); the README calls it the "Drug Repurposing Agent." Same stage, two names.
- Within the pre-flight layer, REST APIs are queried "simultaneously" (some parallel *data fetching*), even though the *reasoning* pipeline is sequential.

*Sources: https://github.com/Paulhb7/orphafold · https://devpost.com/software/orphafold*

---

## 3. Data sources integrated

| Source | Contribution |
|--------|--------------|
| **Orphanet** (orphadata.com) | Rare-disease classification, prevalence, clinical baseline |
| **OMIM** (via NCBI E-utilities) | Mendelian inheritance patterns, gene-disease relationships |
| **UniProt** | Target protein sequences, functional annotation / domains |
| **NCBI Gene** | Gene information / gene-disease associations |
| **ClinVar** | Pathogenic / clinical variant interpretations |
| **AlphaFold DB** | Predicted 3D protein structures + pLDDT confidence scores |
| **PubMed** (NCBI) | Biomedical literature synthesis / bibliography |
| **ClinicalTrials.gov** | Active clinical-trial registry data |
| **DrugBank** | Existing drug-target / compound data (repurposing) |
| **ChEMBL** | Chemical compound / bioactivity data (repurposing) |
| **Google Search Grounding** | General web grounding for clinical context |

*Sources: https://github.com/Paulhb7/orphafold · https://devpost.com/software/orphafold*

---

## 4. Methods

- **Target identification:** Bio-Mechanism Agent maps disease → target proteins → functional domains, retrieves AlphaFold 3D folds, reports pLDDT confidence, and makes a druggability assessment.
- **Structural homology analysis (core novel method):** Compares 3D protein folds across diseases to find diseases "sharing similar structural defects" that could share a drug treatment ("Structural Homology Hypotheses").
- **Binding-pocket analysis:** Comparative analysis of protein binding pockets and 3D folds feeds the repurposing step.
- **Drug-repurposing hypothesis generation:** Bridges mechanism overlap via structural homology, drawing candidates from DrugBank/ChEMBL, producing "in-silico repurposing hypotheses with feasibility scores." Uses the model's extended "thinking budget."
- **Evidence synthesis:** Multi-source API enrichment aggregated and reasoned over by the agents; produces a synthesized bibliography + cross-disease insights, exportable in **RIS format** for reference managers.
- **Scoring / ranking:** "Feasibility scores based on 3D fold similarity" plus pLDDT confidence metrics.
  - ⚠️ **Gap:** the scoring methodology is **not quantitatively specified** — no formula, threshold, or validation of the feasibility score anywhere in the README or Devpost.

*Sources: https://github.com/Paulhb7/orphafold · https://devpost.com/software/orphafold*

---

## 5. Tech stack

**Models**
- Devpost, the README badge, and the tagline all state **Gemini 3** (via Google GenAI SDK / Google AI Studio).
- ⚠️ **Source discrepancy (verified):** the README **body text** literally reads *"…powered by **Gemini 1.5 Pro**."* So branding says "Gemini 3" while the prose says "Gemini 1.5 Pro." Looks like a stale copy-paste; the Gemini 3 Hackathon context + Devpost strongly imply Gemini 3 in practice. Both flagged.

**Frontend (verified from `package.json`)**
- **React 19.2** + React DOM 19.2, **TypeScript ~5.8**, **Vite ^6.2**. Codebase ~98% TypeScript.
- **3D protein visualization:** **`3dmol` v2.5.4** (3Dmol.js) renders interactive `.pdb` structures. Also **Three.js v0.182** + **@react-three/fiber v9.5** + **@react-three/drei v10.7**.
- **d3 v7.9** (timelines / data viz), **lucide-react** (icons), **jquery v4** (a 3Dmol.js dependency).

**AI SDK:** `@google/genai` v1.40.0 (Google GenAI SDK).

**Backend:** No separate backend detailed — frontend-centric, calling external biomedical REST APIs directly, with the pre-flight layer fetching raw data before invoking Gemini.

**Deployment**
- README: "optimized for deployment on **Google AI Studio**."
- Live demo runs on **Google Cloud Run** (`…us-west1.run.app`).

**Devpost "Built With" tags:** Agentic-ai, AlphaFold, ClinVar, Gemini, Google AI Studio, Google GenAI SDK, JavaScript, Orphanet, PubMed, React-Native.
- ⚠️ Devpost tags "React-Native," but the repo is **React 19 web + Vite** (not React Native) — likely a mistag.

**Setup (README):** Node v18+, `make install` / `npm install`, set `GEMINI_API_KEY` in `.env.local`, `make dev` / `npm run dev`.

*Sources: https://github.com/Paulhb7/orphafold (README + package.json) · https://devpost.com/software/orphafold*

---

## 6. Team

**Solo project by Paul Barbaste** (GitHub: **Paulhb7**). Devpost lists 1 team member, 7 likes, 3 comments.

- **Role / employer:** Senior AI Applied Engineer at **Wavestone** (FDE & AI Lab Tech Lead). Based in **Paris, France**.
- **Background:** Co-founder & CTO of neurotech company **Inclusive Brains**; built **Prometheus BCI**, which powered the **Paris 2024 Olympic Torch Relay** (enabling a person with motor disabilities to carry the Olympic flame via brainwaves/facial expressions). Education associated with **HEC Paris** and **École Polytechnique** (site also mentions Sciences Po); teaches a Neurosciences & AI track. Listed among France's top-100 inventors (AI category).
- **Other hackathon record:** 1st place — 2025 IBM TechXchange AI Agent Hackathon (Climate AI Hive, ~5,000 devs); 2nd place — 2025 Hugging Face MCP & Agent Hackathon (Sentinel One); NASA Space Apps 2025 global nominee.

*Sources: https://devpost.com/software/orphafold · https://paulhb7.github.io/ · https://github.com/Paulhb7*

---

## 7. Results / recognition

- **Award:** **Honorable Mention** at the **Gemini 3 Hackathon** — 1 of **10** Honorable Mentions at **$2,000** each. The Devpost page shows a "Winner" badge; the creator also frames this publicly as a **"Top 10"** finish.
- **Hackathon details:** Gemini 3 Hackathon, organized by **Google DeepMind** (managed by Devpost).
  - **Dates:** Dec 17, 2025 – Feb 9, 2026 (OrphaFold submitted Feb 9, 2026).
  - **Scale:** **35,556 participants**.
  - **Prize pool:** $100,000 total — Grand $50k, 2nd $20k, 3rd $10k, plus 10 Honorable Mentions at $2k.
  - **Judging weights:** Technical Execution 40% · Innovation/Wow 30% · Potential Impact 20% · Presentation/Demo 10%.
- **Live demo:** https://orphafold-deep-structural-search-for-rare-disease-61857687256.us-west1.run.app
- **GitHub:** https://github.com/Paulhb7/orphafold (public, ~98% TypeScript)
- **Demo video:** Referenced on Devpost, but a specific YouTube/Vimeo URL was **not found**.

*Sources: https://devpost.com/software/orphafold · https://gemini3.devpost.com/ · https://gemini3.devpost.com/project-gallery*

---

## 8. Devpost narrative sections (near-verbatim)

- **How we built it:** Core is a "Multi-Agent Orchestration layer built on Gemini 3." Frontend in "React/Vite" with a custom "Structural Proteomics Lab using 3D rendering for AlphaFold pdb models." Agent reasoning is "multi-layered," cross-validating clinical registries against structural proteomic data.
- **Challenges we ran into:** The main challenge was "**Data Synthesis latency**." Orchestrating multiple agents querying REST APIs simultaneously required careful prompt engineering "to prevent hallucinations in a medical context." Resolved by a "**Pre-Flight layer** that fetches raw structural data first, feeding it into Gemini as a grounded context."
- **Accomplishments we're proud of:** (1) "Structural Homology Hypotheses" identifying diseases sharing similar structural defects for potential shared drug treatments; (2) seamless mapping of 3D AlphaFold structures to real-time clinical-trial data; (3) researcher-ready **RIS bibliography export** to reference managers.
- **What we learned:** "Gemini 3's reasoning capabilities are a game-changer for scientific research." It "can follow complex biological logic chains," and agentic workflows can "handle the cold start problem in rare disease research."
- **What's next:** Integrate "direct docking simulations" to turn hypotheses into predictive scores inside the agentic loop; explore migrating to the **Agent Development Kit (ADK)** framework; seek beta-testing partnerships with geneticists and rare-disease researchers.

*Source: https://devpost.com/software/orphafold*

---

## 9. Limitations and gaps

- **Prototype only:** self-described "Research Prototype and Proof of Concept"; "decision-support tool, not a clinical system."
- **Rare-disease-only scope:** built around Orphanet / orphan diseases — not a general target-triage tool.
- **No docking / no simulation in-loop:** 3D docking is roadmap only; hypotheses aren't yet turned into simulation-backed predictive scores.
- **Opaque scoring:** "feasibility scores" / "3D fold similarity" are mentioned but never quantitatively defined — no formula, thresholds, or ranking methodology. No deterministic, reproducible scoring layer.
- **No validation:** no benchmark against known drug–disease associations; no sensitivity/specificity or control eval.
- **No MCP:** uses direct REST API calls + Google GenAI SDK + Google Search Grounding. No Model Context Protocol integration. (The creator has done MCP work elsewhere — Sentinel One — but not here.)
- **Reliability/robustness unspecified:** no documented API-failure handling, rate-limiting, or scalability discussion.
- **No LICENSE file** found in the repo (as of research) — open-source status ambiguous.
- **Model ambiguity:** README body says "Gemini 1.5 Pro" while branding says "Gemini 3."

*Sources: https://github.com/Paulhb7/orphafold · https://devpost.com/software/orphafold*

---

## 10. Comparison to a general drug-target-triage instrument (Claude + MCP + deterministic eval)

| Dimension | OrphaFold | A general Claude + MCP + deterministic-eval instrument (e.g. Target Triage) |
|-----------|-----------|------------------------------------------------------------------------------|
| **Scope** | Rare / orphan diseases only (Orphanet-anchored) | General target triage across any disease/target |
| **Model** | Gemini 3 (Google GenAI SDK); README says "1.5 Pro" | Claude, with MCP tool-calling |
| **Tool integration** | Direct REST API calls + Google Search Grounding; **no MCP** | MCP servers as a standardized, reusable tool/data layer |
| **Scoring** | LLM-generated "feasibility scores," methodology undisclosed, non-deterministic | Deterministic, reproducible scoring with defined thresholds |
| **Validation gate** | None reported; no positive-control eval | Controls-first: "nothing is done until the positive-control eval passes" |
| **Orchestration** | 4 sequential Gemini agents + pre-flight grounding | Agent maps + deterministic analysis stages |
| **Reproducibility** | Prototype/PoC, non-reproducible ranking | Reproducibility and printed control numbers are the bar |
| **Output** | Structural reports, 3D pdb viz (3Dmol.js), RIS bibliography export | Evidence-scored triage with auditable, deterministic ranking |

**Bottom line:** OrphaFold is a polished single-domain *demonstration* of Gemini-3 agentic reasoning over public bio databases with excellent structural-biology UX. It differentiates on **3D structural visualization and structural-homology hypotheses**, but leaves open exactly the axes a controls-first instrument owns: **standardized reusable tooling (MCP), reproducible/auditable scoring, a positive-control validation gate, and generality beyond orphan diseases.**

### What to borrow / watch
- **Borrow:** the pre-flight grounding pattern (fetch raw data first, feed as grounded context to cut hallucination) is a clean, cheap reliability win. The 3Dmol.js AlphaFold viewer is a strong demo asset. RIS export is a nice researcher-ready touch.
- **Beat:** their undisclosed, non-deterministic scoring and absent validation — your positive-control eval printing PASS is a direct, defensible contrast.
- **Watch:** "What's next" says they plan docking simulations + ADK migration. If OrphaFold adds a simulation-backed predictive score, its scoring gap narrows — but it stays rare-disease-scoped and MCP-less.

---

## Caveats to carry forward
1. README literally says **"Gemini 1.5 Pro"** in body text despite **"Gemini 3"** branding — flagged, not resolved.
2. Award is **Honorable Mention** (1 of 10, $2,000), also framed by the creator as "Top 10."
3. Devpost tag says **"React-Native"** but the repo is **React 19 web + Vite**.
4. A **demo video** is referenced but no direct video URL was found.
5. **No LICENSE file** found — open-source status ambiguous.

## Sources
- https://devpost.com/software/orphafold
- https://github.com/Paulhb7/orphafold (README + package.json)
- https://github.com/Paulhb7
- https://paulhb7.github.io/
- https://gemini3.devpost.com/
- https://gemini3.devpost.com/project-gallery
