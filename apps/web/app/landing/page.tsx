"use client";

import { Badge, Button, Icon } from "@foundry/ui";
import type { IconName } from "@foundry/ui";
import Link from "next/link";
import { type CSSProperties, useCallback, useEffect, useRef, useState } from "react";
import { ThemeToggle } from "@/components/theme";
import { BRAND } from "@/lib/brand";

/** Entrance animation with a stagger, reusing the shared `fade` keyframe. */
const reveal = (delay: number): CSSProperties => ({ animation: `fade .6s ease both`, animationDelay: `${delay}s` });

const TEAM: { initials: string; role: string; color: string }[] = [
  { initials: "CE", role: "CEO", color: "#7c5cff" },
  { initials: "CT", role: "CTO", color: "#5b8cff" },
  { initials: "PM", role: "Product", color: "#34d3ee" },
  { initials: "BA", role: "Analyst", color: "#3ad29f" },
  { initials: "BE", role: "Backend", color: "#f6c454" },
  { initials: "FE", role: "Frontend", color: "#ff7ac6" },
  { initials: "QA", role: "QA", color: "#ff6b7d" },
  { initials: "DO", role: "DevOps", color: "#9d7bff" },
  { initials: "DS", role: "Designer", color: "#34d3ee" },
  { initials: "SE", role: "Security", color: "#3ad29f" },
];

const PIPELINE: { icon: IconName; label: string }[] = [
  { icon: "doc", label: "Intake" },
  { icon: "pen", label: "Spec" },
  { icon: "cpu", label: "Build" },
  { icon: "shield", label: "Review" },
  { icon: "flask", label: "QA" },
  { icon: "rocket", label: "Ship" },
];

const CAPABILITIES: { icon: IconName; title: string; body: string }[] = [
  { icon: "kanban", title: "Intake to shipped", body: "A full AI team takes a ticket through spec, plan, build, code review, QA, and ship — a decision graph, not a straight line: any verdict can send work back or escalate to the CTO." },
  { icon: "cpu", title: "Parallel, coherent builds", body: "Role-matched engineers build their slices in parallel, each in an isolated git worktree, then merge. When QA or review flags a fault, only the failing part rebuilds." },
  { icon: "flask", title: "QA that runs the real app", body: "The harness boots the app's own server, drives headless Chromium, and checks each acceptance criterion by behaviour. Verdicts are grounded in what actually ran — a hallucinated 'looks good' can't ship." },
  { icon: "gear", title: "Configurable autonomy", body: "Run every mission manual, assisted, supervised, or autonomous. Even autonomous can't force-push or auto-approve a risky merge — you hold the gates that matter." },
  { icon: "plug", title: "Any model — including Claude CLI", body: "Bind Anthropic, OpenAI/Gemini/Groq/Mistral, a local Ollama runtime, or your Claude Code CLI seat per agent, with failover chains, budgets, and live token/cost metering." },
  { icon: "rocket", title: "Run the app in one click", body: "Boot any shipped mission on a free localhost port and open it live — real API and UI, not a snapshot. Plus a built-in ticket board and an optional Jira mirror." },
];

const AUTONOMY: { name: string; body: string; pct: number }[] = [
  { name: "Manual", body: "The team proposes every step and waits for you.", pct: 25 },
  { name: "Assisted", body: "Drafts spec & code; you approve each phase.", pct: 50 },
  { name: "Supervised", body: "Builds & tests autonomously; merge & deploy wait for your sign-off.", pct: 70 },
  { name: "Autonomous", body: "Ships end-to-end within the guardrails you set.", pct: 100 },
];

const INTEGRATIONS = ["Jira", "GitHub", "Slack", "Chrome", "Sentry", "Linear", "Figma", "Vercel"];

/** Real product screenshots (in /public/shots) — the "See it in action" tour cycles through these. */
const SHOTS: { src: string; label: string; caption: string }[] = [
  { src: "/shots/dashboard.png", label: "Dashboard", caption: "Command center — live activity, spend, blockers, and every mission at a glance." },
  { src: "/shots/live-build.png", label: "Live Build", caption: "Watch the pipeline run in real time — phases, the team, and one-click Run app on a shipped build." },
  { src: "/shots/tickets.png", label: "Ticket board", caption: "A Jira-like board the team drives itself: To Do → In Progress → In Review → QA → Done, with the QA evidence behind every verdict." },
  { src: "/shots/models.png", label: "Models", caption: "Bind any provider per agent — Anthropic, OpenAI-compatible, local Ollama, or your Claude Code CLI seat — with failover, budgets, and live metering." },
  { src: "/shots/team.png", label: "Team", caption: "Ten role-locked specialists laid out by the agentic SDLC, each with its own model binding and live status." },
  { src: "/shots/missions.png", label: "Missions", caption: "Every mission with its stage, autonomy level, and full inspectable history — intake to ship." },
];

export default function LandingPage() {
  return (
    <main style={{ minHeight: "100dvh" }}>
      {/* Top nav */}
      <header
        className="row between wrap"
        style={{
          position: "sticky", top: 0, zIndex: 20, padding: "14px 26px", rowGap: 8,
          borderBottom: "1px solid var(--line)",
          background: "color-mix(in srgb, var(--bg) 78%, transparent)",
          backdropFilter: "blur(14px)", WebkitBackdropFilter: "blur(14px)",
        }}
      >
        <div className="row gap-10">
          <div className="brand-logo" style={{ width: 34, height: 34 }}>
            <svg width="20" height="20" viewBox="0 0 64 64" fill="none" aria-hidden="true">
              <path d="M24 20 L14 32 L24 44" stroke="currentColor" strokeWidth={4.5} strokeLinecap="round" strokeLinejoin="round" />
              <path d="M40 20 L50 32 L40 44" stroke="currentColor" strokeWidth={4.5} strokeLinecap="round" strokeLinejoin="round" />
              <path d="M32 23 L33.7 30.3 L41 32 L33.7 33.7 L32 41 L30.3 33.7 L23 32 L30.3 30.3 Z" fill="currentColor" />
            </svg>
          </div>
          <span style={{ fontFamily: "var(--display)", fontWeight: 800, fontSize: 17 }}>{BRAND.name}</span>
        </div>
        <nav className="landing-nav row gap-16">
          <a href="#process" className="nav-link">How it works</a>
          <a href="#tour" className="nav-link">Tour</a>
          <a href="#capabilities" className="nav-link">Product</a>
          <a href="#team" className="nav-link">Team</a>
          <a href="#autonomy" className="nav-link">Autonomy</a>
          <span style={{ width: 1, height: 22, background: "var(--line)", margin: "0 2px" }} aria-hidden />
          <ThemeToggle />
          <Button asChild variant="primary">
            <Link href="/dashboard">Open dashboard</Link>
          </Button>
        </nav>
      </header>

      {/* Hero */}
      <section
        style={{ maxWidth: 1180, margin: "0 auto", padding: "64px 26px 40px", display: "grid", gap: 40, gridTemplateColumns: "1.1fr .9fr", alignItems: "center" }}
        className="hero-grid"
      >
        <div style={reveal(0)}>
          <Badge tone="brand" dot>Autonomous engineering org</Badge>
          <h1 style={{ fontSize: "clamp(38px, 5vw, 60px)", fontWeight: 800, letterSpacing: "-.02em", lineHeight: 1.05, margin: "18px 0 0", maxWidth: "16ch" }}>
            Ship software with a{" "}
            <span style={{ backgroundImage: "var(--grad)", WebkitBackgroundClip: "text", backgroundClip: "text", color: "transparent" }}>
              full AI team
            </span>
            , at the autonomy you set.
          </h1>
          <p style={{ color: "var(--muted)", fontSize: 17, lineHeight: 1.6, marginTop: 18, maxWidth: "52ch" }}>
            Hand {BRAND.name} a ticket from Jira, Linear, GitHub, or plain text. Specialized agents —
            PM, BA, architects, backend, frontend, QA, security, DevOps — take it through the real
            lifecycle and deliver a merged, tested change. Every step is inspectable and gated.
          </p>
          <div className="row wrap gap-10" style={{ marginTop: 26 }}>
            <Button asChild variant="primary" size="lg">
              <Link href="/dashboard"><Icon name="rocket" size={16} /> Open the command center</Link>
            </Button>
            <Button asChild variant="subtle" size="lg">
              <a href="#capabilities">See how it works</a>
            </Button>
          </div>
          <div className="row gap-20" style={{ marginTop: 30 }}>
            <Stat value="10" label="specialist agents" />
            <Stat value="6" label="lifecycle phases" />
            <Stat value="4" label="autonomy levels" />
          </div>
        </div>

        <HeroCard />
      </section>

      {/* Pipeline */}
      <section style={{ maxWidth: 1180, margin: "0 auto", padding: "10px 26px 20px" }}>
        <div className="card pad" style={{ ...reveal(0.1) }}>
          <div className="row wrap between gap-12">
            {PIPELINE.map((p, i) => (
              <div key={p.label} className="row gap-12" style={{ flex: 1, minWidth: 150 }}>
                <div className="row gap-10">
                  <span className="stat-ic" style={{ position: "static", width: 38, height: 38 }}><Icon name={p.icon} size={18} /></span>
                  <div>
                    <div className="text-xs faint">Phase {i + 1}</div>
                    <div className="fw-7">{p.label}</div>
                  </div>
                </div>
                {i < PIPELINE.length - 1 && <span className="faint" style={{ marginLeft: "auto" }}><Icon name="chevron" size={16} /></span>}
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Process reel — the animated "how a mission flows" explainer (a lightweight video-in-code) */}
      <section id="process" style={{ maxWidth: 1180, margin: "0 auto", padding: "44px 26px 8px" }}>
        <SectionHead eyebrow="How it works" title="Watch a mission flow through the org" desc="From a one-line ticket to a shipped, tested app — this is the exact path every mission takes, on repeat." />
        <ProcessReel />
      </section>

      {/* Product tour — real screenshots, auto-cycling like a demo reel */}
      <section id="tour" style={{ maxWidth: 1180, margin: "0 auto", padding: "44px 26px 20px" }}>
        <SectionHead eyebrow="See it in action" title="A guided tour of the product" desc="Real screens from the running app. It plays on its own — or pick a screen to jump straight to it." />
        <ProductTour />
      </section>

      {/* Team */}
      <section id="team" style={{ maxWidth: 1180, margin: "0 auto", padding: "44px 26px" }}>
        <SectionHead eyebrow="The team" title="Ten specialists, one mission each" desc="Each agent owns a role and a model binding, collaborates through a shared ledger and memory, and reports every action." />
        <div className="row wrap gap-12" style={{ marginTop: 22 }}>
          {TEAM.map((t) => (
            <div key={t.role} className="card pad row gap-12" style={{ minWidth: 190, flex: 1 }}>
              <div className="avatar" style={{ background: `linear-gradient(135deg, ${t.color}, ${t.color}cc)` }}>{t.initials}</div>
              <div>
                <div className="fw-7">{t.role}</div>
                <div className="text-xs faint">agent</div>
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* Capabilities */}
      <section id="capabilities" style={{ maxWidth: 1180, margin: "0 auto", padding: "24px 26px" }}>
        <SectionHead eyebrow="Capabilities" title="Everything a real engineering org needs" desc="Not a chatbot — a company. Providers, skills, memory, integrations, and guardrails, working together." />
        <div className="grid g-3" style={{ marginTop: 22 }}>
          {CAPABILITIES.map((c) => (
            <article key={c.title} className="card pad">
              <span className="stat-ic" style={{ position: "static", width: 40, height: 40 }}><Icon name={c.icon} size={19} /></span>
              <h3 style={{ fontSize: 16, marginTop: 12 }}>{c.title}</h3>
              <p className="text-sm muted" style={{ marginTop: 6, lineHeight: 1.55 }}>{c.body}</p>
            </article>
          ))}
        </div>
      </section>

      {/* Autonomy */}
      <section id="autonomy" style={{ maxWidth: 1180, margin: "0 auto", padding: "24px 26px" }}>
        <SectionHead eyebrow="Autonomy" title="You decide how much rope" desc="Set a default for the workspace and override per mission. Higher levels ask for fewer confirmations — the guardrails always hold." />
        <div className="grid g-4" style={{ marginTop: 22 }}>
          {AUTONOMY.map((a) => (
            <article key={a.name} className="card pad">
              <div className="fw-7" style={{ fontFamily: "var(--display)", fontSize: 16 }}>{a.name}</div>
              <div className="progress thin mt-12"><div className="bar" style={{ width: `${a.pct}%` }} /></div>
              <p className="text-sm muted" style={{ marginTop: 12, lineHeight: 1.55 }}>{a.body}</p>
            </article>
          ))}
        </div>
      </section>

      {/* Integrations */}
      <section style={{ maxWidth: 1180, margin: "0 auto", padding: "24px 26px 10px" }}>
        <div className="card pad center">
          <div className="section-label">Plugs into your stack</div>
          <div className="row wrap gap-10" style={{ justifyContent: "center", marginTop: 6 }}>
            {INTEGRATIONS.map((i) => <span key={i} className="chip">{i}</span>)}
          </div>
        </div>
      </section>

      {/* CTA */}
      <section style={{ maxWidth: 1180, margin: "0 auto", padding: "36px 26px 72px" }}>
        <div className="card glow pad center" style={{ padding: "44px 24px" }}>
          <h2 style={{ fontSize: 28, fontWeight: 800, letterSpacing: "-.02em" }}>Dispatch your first mission</h2>
          <p className="muted" style={{ marginTop: 8, maxWidth: "48ch", marginInline: "auto" }}>
            Open the command center, describe a task, and watch the team take it from intake to a merged PR.
          </p>
          <div className="row gap-10" style={{ justifyContent: "center", marginTop: 20 }}>
            <Button asChild variant="primary" size="lg">
              <Link href="/dashboard"><Icon name="rocket" size={16} /> Open the command center</Link>
            </Button>
          </div>
        </div>
        <p className="center faint text-xs" style={{ marginTop: 26 }}>{BRAND.name} — {BRAND.tagline.toLowerCase()}.</p>
      </section>
    </main>
  );
}

function Stat({ value, label }: { value: string; label: string }) {
  return (
    <div>
      <div style={{ fontFamily: "var(--display)", fontWeight: 800, fontSize: 24, lineHeight: 1 }}>{value}</div>
      <div className="text-xs faint" style={{ marginTop: 4 }}>{label}</div>
    </div>
  );
}

function SectionHead({ eyebrow, title, desc }: { eyebrow: string; title: string; desc: string }) {
  return (
    <div style={{ maxWidth: "60ch" }}>
      <div className="section-label c-brand">{eyebrow}</div>
      <h2 style={{ fontSize: 28, fontWeight: 800, letterSpacing: "-.02em" }}>{title}</h2>
      <p className="muted" style={{ marginTop: 8, lineHeight: 1.6 }}>{desc}</p>
    </div>
  );
}

/** Animated "how a mission flows" explainer — a looping, code-driven process reel (no video file).
 *  A pointer walks the pipeline stage by stage; each stage lights up, completes, and updates the
 *  caption + working role. Pauses on click and respects `prefers-reduced-motion`. */
function ProcessReel() {
  const STAGES: { label: string; icon: IconName; who: string; role: string; status: string; caption: string }[] = [
    { label: "Intake", icon: "doc", who: "PM", role: "pm", status: "Framing", caption: "A PM reads the ticket, frames the problem, and scopes the work." },
    { label: "Spec", icon: "pen", who: "PM", role: "pm", status: "Spec", caption: "The spec lands with machine-checkable acceptance criteria." },
    { label: "Build", icon: "cpu", who: "Engineers", role: "backend", status: "Building", caption: "Engineers build their slices in parallel git worktrees, then merge into one codebase." },
    { label: "Review", icon: "shield", who: "CTO", role: "cto", status: "In review", caption: "The CTO reviews correctness, security, and scope — before QA ever runs." },
    { label: "QA", icon: "flask", who: "QA", role: "qa", status: "Verifying", caption: "QA boots the real app, drives a headless browser, and verifies every criterion." },
    { label: "Ship", icon: "rocket", who: "DevOps", role: "devops", status: "Shipped ✓", caption: "DevOps merges and deploys — behind the gate you own." },
  ];
  const N = STAGES.length;
  const ROLE: Record<string, string> = { pm: "#34d3ee", cto: "#5b8cff", backend: "#3ad29f", qa: "#f6c454", devops: "#9d7bff" };
  const [step, setStep] = useState(0);
  const [playing, setPlaying] = useState(true);

  useEffect(() => {
    const reduce = typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    if (reduce) { setPlaying(false); setStep(N - 1); }
  }, [N]);
  useEffect(() => {
    if (!playing) return;
    const id = setInterval(() => setStep((s) => (s + 1) % N), 1650);
    return () => clearInterval(id);
  }, [playing, N]);

  const s = STAGES[step]!;
  const pct = N > 1 ? (step / (N - 1)) * 100 : 0;
  const done = step === N - 1;
  const accent = ROLE[s.role] ?? "var(--brand)";

  return (
    <div className="card" style={{ ...reveal(0.05), overflow: "hidden", padding: 0, marginTop: 22 }}>
      {/* header: the mission being built + its live status */}
      <div className="row between wrap" style={{ padding: "13px 16px", borderBottom: "1px solid var(--line)", background: "var(--panel-2)", gap: 10 }}>
        <div className="row gap-8" style={{ minWidth: 0 }}>
          <span className="dot pulse" style={{ background: done ? "var(--green)" : "var(--brand)" }} />
          <span style={{ fontFamily: "var(--mono)", fontSize: 12, color: "var(--faint)" }}>M-224</span>
          <span className="fw-7" style={{ fontSize: 13.5, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>Realtime team chat widget</span>
        </div>
        <div className="row gap-8">
          <span style={{ display: "inline-flex", alignItems: "center", gap: 6, height: 22, padding: "0 10px", borderRadius: 999, fontSize: 11.5, fontWeight: 800,
            color: done ? "var(--green)" : "var(--brand)", background: `color-mix(in srgb, ${done ? "var(--green)" : "var(--brand)"} 14%, transparent)`,
            border: `1px solid color-mix(in srgb, ${done ? "var(--green)" : "var(--brand)"} 34%, var(--line))` }}>
            {s.status}
          </span>
          <button type="button" aria-label={playing ? "Pause" : "Play"} onClick={() => setPlaying((p) => !p)}
            className="row gap-6" style={{ border: "1px solid var(--line)", background: "var(--panel)", color: "var(--muted)", borderRadius: 8, padding: "4px 9px", cursor: "pointer", fontSize: 12, fontWeight: 700 }}>
            <Icon name={playing ? "shield" : "bolt"} size={12} /> {playing ? "Pause" : "Play"}
          </button>
        </div>
      </div>

      {/* stage track */}
      <div style={{ padding: "30px 26px 8px" }}>
        <div style={{ position: "relative" }}>
          {/* connector line + progress fill, centered on the 46px node circles */}
          <div style={{ position: "absolute", left: 23, right: 23, top: 23, height: 3, borderRadius: 3, background: "var(--line)" }}>
            <div className={`reel-fill${playing ? " animate" : ""}`} style={{ position: "absolute", inset: 0, width: `${pct}%`, borderRadius: 3 }} />
          </div>
          <div className="row between" style={{ position: "relative", alignItems: "flex-start" }}>
            {STAGES.map((st, i) => {
              const isDone = i < step, isActive = i === step;
              return (
                <div key={st.label} className="col" style={{ alignItems: "center", gap: 8, width: 74, textAlign: "center" }}>
                  <span style={{
                    width: 46, height: 46, borderRadius: "50%", display: "grid", placeItems: "center", flex: "0 0 46px",
                    color: isActive ? "#fff" : isDone ? "#fff" : "var(--faint)",
                    background: isActive ? accent : isDone ? "var(--green)" : "var(--panel-2)",
                    border: `2px solid ${isActive || isDone ? "transparent" : "var(--line)"}`,
                    // a persistent ring keeps the active stage distinct from the green "done" nodes
                    // in every frame (the pulse box-shadow alone is invisible at some animation phases)
                    outline: isActive ? `3px solid color-mix(in srgb, ${accent} 38%, transparent)` : "none",
                    outlineOffset: 2,
                    animation: isActive && playing ? "reel-pulse 1.6s ease-out infinite" : undefined,
                    transition: "background .4s ease, color .4s ease",
                  }}>
                    <Icon name={isDone ? "check" : st.icon} size={19} />
                  </span>
                  <span className="fw-7" style={{ fontSize: 12.5, color: isActive ? "var(--text)" : "var(--muted)" }}>{st.label}</span>
                </div>
              );
            })}
          </div>
        </div>

        {/* live caption for the active stage */}
        <div key={step} className="reel-cap row gap-10" style={{ marginTop: 20, alignItems: "center", minHeight: 44 }}>
          <span style={{ display: "inline-flex", alignItems: "center", gap: 6, flex: "0 0 auto", height: 24, padding: "0 10px", borderRadius: 999,
            fontSize: 11.5, fontWeight: 800, color: accent, background: `color-mix(in srgb, ${accent} 14%, transparent)`, border: `1px solid color-mix(in srgb, ${accent} 32%, var(--line))` }}>
            <span style={{ width: 6, height: 6, borderRadius: "50%", background: accent }} /> {s.who}
          </span>
          <span className="text-sm" style={{ color: "var(--muted)", lineHeight: 1.5 }}>{s.caption}</span>
        </div>
      </div>
    </div>
  );
}

/** "See it in action" — an auto-advancing reel of real product screenshots framed in browser
 *  chrome, so it reads like a short demo video without shipping a heavy video file. Pauses on hover
 *  or when the tab is hidden, and respects `prefers-reduced-motion` (no autoplay). */
function ProductTour() {
  const [i, setI] = useState(0);
  const [playing, setPlaying] = useState(true);
  const [loaded, setLoaded] = useState<Record<number, boolean>>({});
  const hovering = useRef(false);
  const n = SHOTS.length;
  const advance = useCallback(() => setI((p) => (p + 1) % n), [n]);

  useEffect(() => {
    const reduce = typeof window !== "undefined"
      && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    if (reduce) setPlaying(false);
  }, []);

  useEffect(() => {
    if (!playing) return;
    const id = setInterval(() => {
      if (!hovering.current && !document.hidden) advance();
    }, 4200);
    return () => clearInterval(id);
  }, [playing, advance]);

  const shot = SHOTS[i]!; // i is always kept within 0..n-1 by advance()/setI
  return (
    <div style={{ marginTop: 22 }}>
      <div
        className="card"
        style={{ ...reveal(0.05), overflow: "hidden", padding: 0 }}
        onMouseEnter={() => { hovering.current = true; }}
        onMouseLeave={() => { hovering.current = false; }}
      >
        {/* faux browser chrome */}
        <div className="row between" style={{ padding: "11px 14px", borderBottom: "1px solid var(--line)", background: "var(--panel-2)" }}>
          <div className="row gap-8">
            <span style={{ width: 11, height: 11, borderRadius: "50%", background: "#ff5f57" }} />
            <span style={{ width: 11, height: 11, borderRadius: "50%", background: "#febc2e" }} />
            <span style={{ width: 11, height: 11, borderRadius: "50%", background: "#28c840" }} />
          </div>
          <div className="row gap-8" style={{ flex: 1, justifyContent: "center", minWidth: 0 }}>
            <span className="chip" style={{ maxWidth: "70%", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontFamily: "var(--mono)", fontSize: 11.5 }}>
              {`app.${BRAND.name.toLowerCase()}.dev/dashboard/${shot.label.toLowerCase().replace(/\s+/g, "-")}`}
            </span>
          </div>
          <button
            type="button"
            aria-label={playing ? "Pause tour" : "Play tour"}
            onClick={() => setPlaying((p) => !p)}
            className="row gap-6"
            style={{ border: "1px solid var(--line)", background: "var(--panel)", color: "var(--muted)", borderRadius: 8, padding: "4px 9px", cursor: "pointer", fontSize: 12, fontWeight: 700 }}
          >
            <Icon name={playing ? "shield" : "bolt"} size={12} /> {playing ? "Pause" : "Play"}
          </button>
        </div>

        {/* stage — crossfading screenshots */}
        <div style={{ position: "relative", width: "100%", aspectRatio: "16 / 10", background: "var(--panel-2)" }}>
          {SHOTS.map((s, k) => (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              key={s.src}
              src={s.src}
              alt={`${s.label} — ${s.caption}`}
              loading={k === 0 ? "eager" : "lazy"}
              onLoad={() => setLoaded((m) => ({ ...m, [k]: true }))}
              style={{
                position: "absolute", inset: 0, width: "100%", height: "100%", objectFit: "cover", objectPosition: "top center",
                opacity: k === i ? 1 : 0, transition: "opacity .7s ease", pointerEvents: "none",
              }}
            />
          ))}
          {!loaded[i] && (
            <div className="center" style={{ position: "absolute", inset: 0, color: "var(--faint)" }}>
              <Icon name="bolt" size={22} />
            </div>
          )}
        </div>

        {/* caption — a dedicated bar UNDER the screenshot, so it never overlaps the shot's own UI */}
        <div className="row gap-10" style={{ alignItems: "center", padding: "12px 16px", borderTop: "1px solid var(--line)", background: "var(--panel-2)", minHeight: 54 }}>
          <Badge tone="brand" style={{ flex: "0 0 auto" }}>{shot.label}</Badge>
          <span key={i} className="reel-cap text-sm" style={{ color: "var(--muted)", lineHeight: 1.45 }}>{shot.caption}</span>
        </div>
      </div>

      {/* screen tabs */}
      <div className="row wrap gap-8" style={{ marginTop: 12, justifyContent: "center" }}>
        {SHOTS.map((s, k) => (
          <button
            key={s.src}
            type="button"
            onClick={() => setI(k)}
            aria-current={k === i}
            style={{
              border: `1px solid ${k === i ? "var(--brand)" : "var(--line)"}`,
              background: k === i ? "color-mix(in srgb, var(--brand) 14%, transparent)" : "var(--panel)",
              color: k === i ? "var(--brand)" : "var(--muted)",
              borderRadius: 999, padding: "6px 13px", cursor: "pointer", fontSize: 12.5, fontWeight: 700,
              transition: "all .18s ease",
            }}
          >
            {s.label}
          </button>
        ))}
      </div>
    </div>
  );
}

/** Stylized "live build" preview — pure decoration, on-brand and lightly animated. */
function HeroCard() {
  const steps = [
    { label: "Read ticket & frame the work", state: "done" },
    { label: "Draft spec & acceptance stories", state: "done" },
    { label: "Implement the change (TDD)", state: "active" },
    { label: "Run acceptance checks", state: "queued" },
    { label: "Merge & deploy", state: "gated" },
  ];
  return (
    <div className="card" style={{ ...reveal(0.15), overflow: "hidden" }}>
      <div className="card-head">
        <h3 className="row gap-8"><span className="dot pulse bg-brand" /> Live Build · FND-142</h3>
        <span className="badge tint-brand">supervised</span>
      </div>
      <div className="card-body col gap-8">
        {steps.map((s) => (
          <div key={s.label} className={`lb-step is-${s.state}`} style={{ display: "flex", alignItems: "center", gap: 10, padding: "9px 11px", border: "1px solid var(--line)", borderRadius: 11, background: "var(--panel-2)" }}>
            <span className="lb-dot" style={{ width: 9, height: 9, borderRadius: "50%", flex: "0 0 9px", background: s.state === "done" ? "var(--green)" : s.state === "active" ? "var(--brand)" : s.state === "gated" ? "var(--amber)" : "var(--faint)" }} />
            <span style={{ flex: 1, fontWeight: 600, fontSize: 13 }}>{s.label}</span>
            {s.state === "active" && <Icon name="bolt" size={14} />}
            {s.state === "gated" && <Icon name="shield" size={14} />}
          </div>
        ))}
        <div className="lb-metering row gap-16" style={{ marginTop: 6, paddingTop: 12, borderTop: "1px solid var(--line)", color: "var(--faint)", fontFamily: "var(--mono)", fontSize: 12 }}>
          <span>12.4k tokens</span><span>$0.18</span><span>3 agents</span>
        </div>
      </div>
    </div>
  );
}
