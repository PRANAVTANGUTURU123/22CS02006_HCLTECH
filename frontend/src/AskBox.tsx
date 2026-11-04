import { useState } from "react";

interface AskResult {
  answer: string;
  ok: boolean;
  tools_used: string[];
}

export function AskBox() {
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<AskResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function ask(q: string) {
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const response = await fetch("/api/ask", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: q }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(typeof data.detail === "string" ? data.detail : `HTTP ${response.status}`);
      }
      setResult(data as AskResult);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not ask");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section>
      <h2>Ask about the fleet</h2>
      <p className="muted small">
        An AI answers from the live data, using our read-only MCP tools.
      </p>
      <form
        className="ask"
        onSubmit={(e) => {
          e.preventDefault();
          if (question.trim().length >= 3) void ask(question.trim());
        }}
      >
        <input
          aria-label="Question"
          value={question}
          maxLength={300}
          placeholder="e.g. Which trucks are in trouble right now?"
          onChange={(e) => setQuestion(e.target.value)}
        />
        <button type="submit" disabled={busy || question.trim().length < 3}>
          {busy ? "Asking…" : "Ask"}
        </button>
      </form>
      {result && (
        <div className={result.ok ? "ai answer" : "answer warn"}>
          <p>{result.answer}</p>
          {result.tools_used.length > 0 && (
            <p className="muted small">MCP tools used: {result.tools_used.join(", ")}</p>
          )}
        </div>
      )}
      {error && <p className="error">{error}</p>}
    </section>
  );
}
