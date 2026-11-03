import { useCallback, useEffect, useState, type FormEvent } from "react";
import { ApiError, complete, getHealth, type Completion, type Health } from "./api";

function describe(err: unknown): string {
  if (err instanceof ApiError) return `${err.code}: ${err.message}`;
  if (err instanceof Error) return err.message;
  return String(err);
}

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);

  const [prompt, setPrompt] = useState("Say hello in one sentence.");
  const [answer, setAnswer] = useState<Completion | null>(null);
  const [llmError, setLlmError] = useState<string | null>(null);
  const [asking, setAsking] = useState(false);

  const checkHealth = useCallback(async () => {
    setChecking(true);
    setHealthError(null);
    try {
      setHealth(await getHealth());
    } catch (err) {
      setHealth(null);
      setHealthError(describe(err));
    } finally {
      setChecking(false);
    }
  }, []);

  useEffect(() => {
    void checkHealth();
  }, [checkHealth]);

  async function ask(e: FormEvent) {
    e.preventDefault();
    setAsking(true);
    setLlmError(null);
    setAnswer(null);
    try {
      setAnswer(await complete(prompt));
    } catch (err) {
      setLlmError(describe(err));
    } finally {
      setAsking(false);
    }
  }

  const ok = health?.status === "ok";

  return (
    <main>
      <h1>Hackathon Starter</h1>

      <section className="card">
        <div className="row">
          <h2>Backend health</h2>
          <button onClick={() => void checkHealth()} disabled={checking}>
            {checking ? "Checking…" : "Refresh"}
          </button>
        </div>
        {healthError && <p className="error">Unreachable: {healthError}</p>}
        {health && (
          <>
            <p className={ok ? "ok" : "warn"}>
              <span className="dot" aria-hidden /> {health.status.toUpperCase()}
            </p>
            <dl>
              <dt>App</dt>
              <dd>
                {health.app} v{health.version}
              </dd>
              <dt>Environment</dt>
              <dd>{health.environment}</dd>
              <dt>Database</dt>
              <dd>{health.database}</dd>
              <dt>LLM provider</dt>
              <dd>{health.llm_provider}</dd>
              <dt>Server time</dt>
              <dd>{new Date(health.time).toLocaleString()}</dd>
            </dl>
          </>
        )}
      </section>

      <section className="card">
        <h2>LLM test</h2>
        <form onSubmit={ask}>
          <label htmlFor="prompt">Prompt</label>
          <textarea
            id="prompt"
            rows={3}
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            maxLength={8000}
          />
          <button type="submit" disabled={asking || !prompt.trim()}>
            {asking ? "Asking…" : "Send"}
          </button>
        </form>
        {llmError && <p className="error">{llmError}</p>}
        {answer && (
          <div className="answer">
            <p className="meta">
              {answer.provider} · {answer.model} · {Math.round(answer.latency_ms)} ms
              {answer.fallback_used && " · fallback used"}
            </p>
            <p>{answer.text}</p>
          </div>
        )}
      </section>
    </main>
  );
}
