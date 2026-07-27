---
url: https://openai.com/index/gpt-5-6
source_type: newsroom
lab: OpenAI
published_at: 2026-07-09
retrieved_at: 2026-07-24
fetch_method: manual capture (JS-rendered page; static fetch returns an empty shell — see plan §20.8)
---
GPT-5.6: Frontier intelligence that scales with your ambition | OpenAI
July 9, 2026
Product
Release
GPT‑5.6: Frontier intelligence that scales with your ambition
More intelligence from every token, stronger performance per dollar, and more capability on demand for your hardest work.

We’re launching the GPT‑5.6 family of models for general availability following our limited preview: our new flagship, Sol, alongside Terra, a balanced model for everyday work, and Luna, our most cost-efficient model.

GPT‑5.6 Sol sets a new standard for both intelligence and efficiency, achieving state-of-the-art results across coding, knowledge work, cybersecurity, and science while outperforming previous and competing frontier models with fewer tokens and at lower estimated cost. The result is stronger performance per dollar: more successful work for the same spend, or comparable results at a lower total cost. We also introduce a new way to accelerate the most demanding work: ultra is our highest-capability setting, coordinating multiple agents across parallel workstreams to finish complex tasks faster. Stronger computer use and design judgment make GPT‑5.6 Sol our most polished collaborator yet, helping it inspect, refine, and deliver ready-to-use results.

We trained GPT‑5.6 to get more useful work from every token. On Agents’ Last Exam, an evaluation of long-running professional workflows across 55 fields, GPT‑5.6 Sol sets a new high of 53.6, eclipsing Claude Fable 5 (adaptive reasoning) by 13.1 points. Even at medium reasoning, it beats Fable 5 by 11.4 points at roughly one-quarter the estimated cost. That efficiency extends to smaller models, which are essential to making intelligence more abundant and affordable: GPT‑5.6 Terra and GPT‑5.6 Luna outperform Fable 5 at around one-sixteenth the cost. On the Artificial Analysis Intelligence Index, a broad measure of intelligence spanning agentic work, coding, scientific reasoning, and general capabilities, GPT‑5.6 Sol with max reasoning comes within one point of Fable 5 while completing tasks in 61% less time at roughly half the estimated cost.

GPT‑5.6 launches with our most robust safeguards to date, designed to be resilient against determined and adaptive misuse without broadly limiting legitimate work. Before general availability, we put the models and safeguards through our most extensive evaluation period yet, combining human red teaming with large-scale automated testing. During the preview, we worked closely with expert organizations and with trusted partners to pressure-test defenses and strengthen safeguards before broader launch. The resulting system layers protections trained into the model with real-time checks, monitoring, and access calibrated to trust and risk.

Efficient by default, maximum performance on demand
GPT‑5.6 Sol is our best coding model yet. On the Artificial Analysis Coding Agent Index, GPT‑5.6 Sol with max reasoning sets a new state of the art at 80, 2.8 points above Fable 5, while using less than half the output tokens, taking less than half the time, and costing about one-third less. That advantage extends across the family: Terra performs just above Fable 5, while Luna outperforms Opus 4.8; each does so in roughly one-third of the time, with about half as many output tokens, and at approximately one-quarter the estimated cost. It also sets new state-of-the-art results on Terminal‑Bench 2.1 and DeepSWE, which test complex command-line workflows and long-horizon engineering in real codebases.

GPT‑5.6 can write and run lightweight programs that coordinate tools, process intermediate results, monitor progress, and choose the next action as work unfolds. This lets tool-heavy tasks advance with fewer tokens, fewer model round trips, and less guidance. Instead of requiring developers to script every step or passing every tool response back through the model, Programmatic Tool Calling in the Responses API can filter large amounts of intermediate data, retain only what matters, and adapt its workflow along the way.

For problems that reward a greater investment of time and compute, GPT‑5.6 can push beyond this efficient default. max gives GPT‑5.6 even more time than xhigh to reason and explore alternatives, run checks, and revise its approach. ultra goes further by coordinating four agents in parallel by default, trading higher token use for stronger results and faster time-to-result on demanding tasks.

A leap forward in design
GPT‑5.6 delivers a step change in design judgment. With only high-level direction, GPT‑5.6 creates tasteful, ergonomic, and functional interfaces. Its stronger computer-use capabilities let it inspect and refine the rendered result—not just generate the underlying code or content—so it can catch visual and functional issues and apply finishing touches before handing the work back. GPT‑5.6’s frontend capabilities also turn natural-language requests into polished, interactive explanations and visualizations within ChatGPT Work.

End-to-end knowledge work
GPT‑5.6 delivers better results for professional tasks. It takes messy context from your documents and everyday workflows like Slack, Notion, Microsoft 365, and Google Drive, and converts it into expert-level, shareable artifacts. GPT‑5.6’s strength on knowledge work shows up in evaluations spanning long-horizon professional analysis, browsing, tool use, and computer use. GPT‑5.6 Sol sets new state-of-the-art results on BrowseComp at 92.2% and OSWorld 2.0 at 62.6%; on OSWorld, it surpasses Opus 4.8 while using 85% fewer output tokens. Here, the performance-per-dollar gains extend across the GPT‑5.6 family. Luna nearly matches GPT‑5.5’s peak performance at less than half the estimated cost, while Terra surpasses it at a lower cost.

Pushing the frontier on cyber and science
GPT‑5.6 is our strongest cybersecurity model yet, achieving frontier performance with significantly fewer tokens. On ExploitBench2, which measures progress from reaching vulnerable code through arbitrary code execution, it scores 73.5% versus GPT‑5.5’s 47.9% at a comparable output-token budget. On ExploitGym3, which asks agents to turn real-world vulnerabilities into working exploits, it almost doubles GPT‑5.5’s peak pass rate, from 15.1% to 24.9% under the two-hour cap; with six hours, it reaches 33.7%. On SEC-Bench Pro, which tests proof-of-concept generation on complex software, it scores 71.2% versus GPT‑5.5’s 45.8% at an improved latency.

GPT‑5.6 supports important defensive tasks such as secure code review, patching, threat modeling, and blue teaming. Qualified individuals and organizations in OpenAI Daybreak’s Trusted Access for Cyber program can access more of its defensive capability through more precise safeguards for verified work in authorized environments, including vulnerability triage and validation, malware analysis, detection engineering, and patch validation.

GPT‑5.6 Sol also shows broad gains across scientific research. On life sciences evaluations, GPT‑5.6 demonstrates Pareto improvements over GPT‑5.5 on real-world biology, life science research workflows, and chemistry.

GPT‑5.6 accelerates OpenAI
GPT‑5.6 is our strongest model yet for accelerating AI research. Inside OpenAI, researchers use it across the development loop: diagnosing failures, optimizing training systems, running experiments, and interpreting results. We already saw that acceleration and stronger adoption during the internal testing period of GPT‑5.6, as average daily output tokens per active researcher were more than twice the highest level observed for GPT‑5.5.

This way of working is quickly becoming standard. Over the past six months, the share of research compute devoted to internal coding inference grew 100-fold, while internal agentic token usage increased approximately 22-fold. These adoption metrics do not measure research progress on their own, but they show how rapidly AI assistance is increasing for research and across other teams like sales, marketing, user ops, finance, and more.

Scaling safety and security with capability
As model capabilities increase, we strengthen our safety stack so advanced intelligence can remain broadly useful while applying greater scrutiny to the highest-risk uses. For GPT‑5.6, we built our most robust safety system to date, calibrated to each model’s capabilities and powered by more compute than ever before.

The GPT‑5.6 models are more capable than our earlier models in both biology and cybersecurity but do not cross the Critical threshold in either category. In cybersecurity, our testing suggests GPT‑5.6 is better at finding and fixing vulnerabilities than at reliably carrying out autonomous, end-to-end attacks against hardened targets—giving defenders an opportunity to strengthen systems before weaknesses are exploited. In biology, our testing suggests GPT‑5.6 can support legitimate research but does not provide the end-to-end capability needed to create, engineer, or synthesize a highly dangerous novel threat.

Before general availability, we ran our most intensive safety evaluations to date, including extensive red teaming, robust capability and safeguard testing with external experts, and approximately 700,000 NVIDIA A100 Tensor Core GPU-equivalent hours of black-box automated red teaming. This enabled us to systematically probe likely weak points, surface jailbreaks, and help us strengthen the system before launch.

Availability and pricing
GPT‑5.6 spans three model tiers: Sol, our flagship; Terra, a lower-cost model with performance competitive with GPT‑5.5; and Luna, our fastest and most affordable model. The number identifies the generation, while Sol, Terra, and Luna are durable capability tiers that can advance on their own cadence.

GPT‑5.6 is available starting today across ChatGPT, Codex, and the OpenAI API. The rollout is starting globally now and will continue gradually toward full availability over the next 24 hours.

GPT‑5.6 is priced per 1M tokens across three model sizes: Sol is $5 input / $30 output; Terra is $2.50 input / $15 output; and Luna is $1 input / $6 output. GPT‑5.6 also introduces more predictable prompt caching, including support for explicit cache breakpoints and a 30-minute minimum cache life. For GPT‑5.6 and later models, cache writes are billed at 1.25x the model’s uncached input rate, while cache reads continue to receive the 90% cached-input discount.
