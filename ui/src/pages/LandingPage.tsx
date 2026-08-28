import { ArrowUpRight, Github } from 'lucide-react'
import { Link } from 'react-router-dom'

const GITHUB_URL = 'https://github.com/molecularmodelinglab/SABLE'
const PAPER_URL = 'https://arxiv.org/abs/2608.11483'

export function LandingPage() {
  return (
    <div className="landing-page">
      <header className="landing-nav">
        <Link className="landing-wordmark" to="/" aria-label="SABLE home">SABLE</Link>
        <div className="landing-nav__links">
          <a href={PAPER_URL} target="_blank" rel="noreferrer">Paper</a>
          <a href={GITHUB_URL} target="_blank" rel="noreferrer">GitHub</a>
          <Link className="landing-login" to="/login">Log in <ArrowUpRight size={15} aria-hidden="true" /></Link>
        </div>
      </header>

      <main>
        <section className="landing-hero" aria-labelledby="landing-title">
          <div className="landing-hero__copy">
            <h1 id="landing-title">Find optimized molecules instantly.</h1>
            <p className="landing-hero__summary">
             SABLE is an open-source agentic system that translates natural language prompts into a downstream hit-to-lead optimization campaign. All
              recommended molecules are produced from real-life building blocks with known reaction pathways.
            </p>
          </div>
        </section>

        <section className="landing-product" aria-labelledby="product-title">
          <div className="landing-product__heading">
            <h2 id="product-title">
              Launch and monitor molecular optimization campaigns from one workspace.
            </h2>
          </div>
          <figure className="landing-product__figure">
            <img src="/platform-dashboard.png" alt="SABLE campaign dashboard showing optimization run metrics and history" />
            <figcaption>Campaign dashboard</figcaption>
          </figure>
        </section>

        <section className="landing-cta" aria-labelledby="cta-title">
          <h2 id="cta-title">Start a lead optimization campaign.</h2>
          <Link to="/login">Enter SABLE <ArrowUpRight size={24} aria-hidden="true" /></Link>
        </section>
      </main>

      <footer className="landing-footer">
        <Link className="landing-wordmark" to="/">SABLE</Link>
        <p>Managed by 
            <a href="https://kelvinpaschal.com" target="_blank" rel="noreferrer">Kelvin Idanwekhai</a> of
            <a href="https://molecularmodelinglab.github.io/" target="_blank" rel="noreferrer">The Molecular Modeling Lab @ UNC </a>
            </p>
        <div>
          <a href={GITHUB_URL} target="_blank" rel="noreferrer"><Github size={17} aria-hidden="true" /> GitHub</a>
          <a href={PAPER_URL} target="_blank" rel="noreferrer">arXiv <ArrowUpRight size={15} aria-hidden="true" /></a>
        </div>
      </footer>
    </div>
  )
}