import { useEffect, useMemo, useState, type FormEvent } from 'react';
import { getHealth, getSamples, resolveAddress } from './api';
import type { HealthResponse, ResolveResponse, SampleAddress } from './types';

const emptyResult: ResolveResponse | null = null;

function confidenceCopy(level: ResolveResponse['confidence_label'] | undefined) {
  if (level === 'high') return 'High confidence';
  if (level === 'medium') return 'Medium confidence';
  return 'Low confidence';
}

function confidenceTone(level: ResolveResponse['confidence_label'] | undefined) {
  if (level === 'high') return 'tone-high';
  if (level === 'medium') return 'tone-medium';
  return 'tone-low';
}

function mapEmbedUrl(point: ResolveResponse['chosen_point']) {
  if (!point) return '';
  const padding = 0.01;
  const south = point.latitude - padding;
  const west = point.longitude - padding;
  const north = point.latitude + padding;
  const east = point.longitude + padding;
  return `https://www.openstreetmap.org/export/embed.html?bbox=${west}%2C${south}%2C${east}%2C${north}&layer=mapnik&marker=${point.latitude}%2C${point.longitude}`;
}

function App() {
  const [address, setAddress] = useState('');
  const [city, setCity] = useState('');
  const [state, setState] = useState('');
  const [result, setResult] = useState<ResolveResponse | null>(emptyResult);
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [samples, setSamples] = useState<SampleAddress[]>([]);
  const [loading, setLoading] = useState(false);
  const [startupLoading, setStartupLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;

    async function loadBootstrapData() {
      try {
        const [healthResponse, sampleResponse] = await Promise.all([getHealth(), getSamples()]);
        if (cancelled) return;
        setHealth(healthResponse);
        setSamples(sampleResponse);
      } catch (cause) {
        if (!cancelled) {
          setError(cause instanceof Error ? cause.message : 'Failed to reach the backend.');
        }
      } finally {
        if (!cancelled) {
          setStartupLoading(false);
        }
      }
    }

    loadBootstrapData();
    return () => {
      cancelled = true;
    };
  }, []);

  const selectedMapUrl = useMemo(() => mapEmbedUrl(result?.chosen_point ?? null), [result]);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setLoading(true);
    setError('');

    try {
      const response = await resolveAddress({ address, city: city || undefined, state: state || undefined });
      setResult(response);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Resolution failed.');
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="page-shell">
      <div className="page-background" />
      <main className="app-grid">
        <section className="hero-panel">
          <div className="eyebrow">AI BUILD 2026 · Pata</div>
          <h1>Turn messy Indian address text into an evidence-backed geocode.</h1>
          <p className="hero-copy">
            This MVP parses a raw address, cross-checks pincode ground truth, queries live OpenStreetMap landmarks,
            and returns a confidence-scored delivery point with an audit trail.
          </p>

          <div className="metric-row">
            <div className="metric-card">
              <span>Latency target</span>
              <strong>&lt; 500 ms</strong>
            </div>
            <div className="metric-card">
              <span>Decision mode</span>
              <strong>Fast, explainable</strong>
            </div>
            <div className="metric-card">
              <span>Fallback behavior</span>
              <strong>Flag low confidence</strong>
            </div>
          </div>

          <div className="status-card">
            <span className={`status-dot ${health?.dataset ? 'ready' : 'pending'}`} />
            <div>
              <strong>{startupLoading ? 'Checking backend...' : health?.status === 'ok' ? 'Backend ready' : 'Dataset missing'}</strong>
              <p>
                {health?.dataset
                  ? `${health.dataset.rows.toLocaleString()} rows loaded from ${health.dataset.dataset_path}`
                  : health?.error ?? 'Add the Kaggle CSV to backend/data to enable geocoding.'}
              </p>
            </div>
          </div>

          <div className="sample-strip">
            {samples.map((sample) => (
              <button
                key={sample.label}
                type="button"
                className="sample-chip"
                onClick={() => setAddress(sample.address)}
              >
                <span>{sample.label}</span>
                <small>{sample.address}</small>
              </button>
            ))}
          </div>
        </section>

        <section className="workspace-panel">
          <form className="address-form" onSubmit={handleSubmit}>
            <label>
              Messy address
              <textarea
                value={address}
                onChange={(event) => setAddress(event.target.value)}
                placeholder="Opposite Ganesh temple, near Shivaji Nagar colony, Pune 411001"
                rows={6}
              />
            </label>

            <div className="form-grid">
              <label>
                City hint
                <input value={city} onChange={(event) => setCity(event.target.value)} placeholder="Pune" />
              </label>
              <label>
                State hint
                <input value={state} onChange={(event) => setState(event.target.value)} placeholder="Maharashtra" />
              </label>
            </div>

            <button type="submit" className="primary-button" disabled={loading || address.trim().length < 3}>
              {loading ? 'Resolving...' : 'Resolve address'}
            </button>

            {error ? <div className="inline-error">{error}</div> : null}
          </form>

          {result ? (
            <div className="result-stack">
              <article className="result-card emphasis-card">
                <div className="result-header">
                  <div>
                    <p className="section-label">Resolution result</p>
                    <h2>{confidenceCopy(result.confidence_label)}</h2>
                  </div>
                  <div className={`confidence-badge ${confidenceTone(result.confidence_label)}`}>
                    {Math.round(result.confidence * 100)}%
                  </div>
                </div>

                {result.low_confidence ? <div className="warning-banner">Low confidence is intentional here: the app shows the evidence instead of guessing.</div> : null}

                <div className="result-grid">
                  <div className="detail-block">
                    <span>Original address</span>
                    <p>{result.original_address}</p>
                  </div>
                  <div className="detail-block">
                    <span>Normalized address</span>
                    <p>{result.normalized_address}</p>
                  </div>
                  <div className="detail-block">
                    <span>Extracted pincode</span>
                    <p>{result.extracted.pincode ?? 'Not found'}</p>
                  </div>
                  <div className="detail-block">
                    <span>Runtime</span>
                    <p>{result.audit.duration_ms.toFixed(2)} ms</p>
                  </div>
                </div>
              </article>

              <article className="result-card split-card">
                <div>
                  <p className="section-label">Chosen point</p>
                  {result.chosen_point ? (
                    <>
                      <h3>{result.chosen_point.name ?? result.chosen_point.source}</h3>
                      <p>
                        {result.chosen_point.latitude.toFixed(6)}, {result.chosen_point.longitude.toFixed(6)}
                      </p>
                      <p className="muted-text">
                        Source: {result.chosen_point.source}
                        {result.chosen_point.district ? ` · ${result.chosen_point.district}` : ''}
                        {result.chosen_point.state ? ` · ${result.chosen_point.state}` : ''}
                      </p>
                    </>
                  ) : (
                    <p>No point could be safely selected.</p>
                  )}
                </div>

                <div className="map-frame">
                  {selectedMapUrl ? (
                    <iframe
                      title="OpenStreetMap preview"
                      src={selectedMapUrl}
                      loading="lazy"
                      referrerPolicy="no-referrer-when-downgrade"
                    />
                  ) : (
                    <div className="map-placeholder">Map preview appears after a geocode is selected.</div>
                  )}
                </div>
              </article>

              <article className="result-card">
                <p className="section-label">Evidence trail</p>
                <div className="evidence-list">
                  {result.evidence.map((item) => (
                    <div key={`${item.label}-${item.value}`} className="evidence-row">
                      <strong>{item.label}</strong>
                      <span>{item.value}</span>
                    </div>
                  ))}
                </div>
              </article>

              <article className="result-card">
                <p className="section-label">Nearby landmarks</p>
                <div className="candidate-list">
                  {result.candidates.length ? (
                    result.candidates.map((candidate) => (
                      <div key={`${candidate.name}-${candidate.latitude}`} className="candidate-card">
                        <strong>{candidate.name}</strong>
                        <span>{candidate.kind}</span>
                        <span>{candidate.distance_m.toFixed(0)} m away</span>
                        <span>Score: {candidate.score.toFixed(3)}</span>
                      </div>
                    ))
                  ) : (
                    <p className="muted-text">No strong landmark match was returned for this address.</p>
                  )}
                </div>
              </article>

              <article className="result-card">
                <p className="section-label">Self-check</p>
                <ul className="check-list">
                  {result.self_check.map((line) => (
                    <li key={line}>{line}</li>
                  ))}
                </ul>
              </article>
            </div>
          ) : (
            <div className="result-card empty-state">
              <p className="section-label">Output</p>
              <h3>Run one address through the resolver</h3>
              <p>
                The backend will cross-check the pincode directory, query live OpenStreetMap landmarks, and show the
                selected point plus the supporting evidence.
              </p>
            </div>
          )}
        </section>
      </main>
    </div>
  );
}

export default App;
