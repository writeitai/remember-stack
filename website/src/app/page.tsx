import Image from "next/image";
import Link from "next/link";
import { ArrowRight, Github } from "lucide-react";

export default function Home() {
  return (
    <div className="relative isolate overflow-hidden">
      <div
        aria-hidden
        className="absolute left-1/2 top-0 -z-10 h-80 w-80 -translate-x-[115%] rounded-full bg-brand-soft/80 blur-3xl"
      />
      <div
        aria-hidden
        className="absolute left-1/2 top-28 -z-10 h-72 w-72 translate-x-[15%] rounded-full bg-teal-soft blur-3xl"
      />

      <div className="container mx-auto flex flex-col items-center px-4 py-20 text-center sm:py-28">
        <Image
          src="/brand/mark.svg"
          width={800}
          height={680}
          alt=""
          aria-hidden
          priority
          className="mb-7 h-24 w-auto sm:h-28"
        />
        <span className="mb-6 inline-flex items-center gap-2 rounded-full border border-border bg-card/90 px-3 py-1 text-xs font-medium text-muted-foreground shadow-sm">
          <span className="h-1.5 w-1.5 rounded-full bg-coral" aria-hidden />
          Open source · Memory infrastructure for AI agents
        </span>

        <h1 className="font-display max-w-3xl text-4xl font-bold tracking-[-0.035em] sm:text-5xl sm:leading-[1.08]">
          A memory system for AI agents, built for millions of documents.
        </h1>

        <p className="mt-6 max-w-2xl text-lg leading-8 text-muted-foreground">
          <span className="font-semibold text-foreground">RememberStack</span>{" "}
          ingests heterogeneous documents — files, mail, recordings, images —
          and distills them into progressively more abstract, navigable
          knowledge: immutable evidence, adjudicated facts, and compiled
          understanding. Every answer traces back to its sources, and everything
          stays auditable by humans.
        </p>

        <div className="mt-10 flex flex-col items-center gap-4 sm:flex-row">
          <Link
            href="/docs"
            className="group inline-flex items-center gap-2 rounded-md bg-primary px-5 py-2.5 text-sm font-medium text-primary-foreground shadow-[0_8px_20px_rgba(16,31,74,0.14)] outline-none transition-transform hover:-translate-y-0.5 focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-4"
          >
            Read the docs
            <ArrowRight className="h-4 w-4 text-coral-lit transition-transform group-hover:translate-x-0.5" />
          </Link>
          <a
            href="https://github.com/writeitai/remember-stack"
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-2 rounded-md border border-border bg-card/70 px-5 py-2.5 text-sm font-medium text-foreground outline-none transition-colors hover:bg-card focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-4"
          >
            <Github className="h-4 w-4 text-teal" />
            View on GitHub
          </a>
        </div>

        <div className="mt-12 w-full max-w-lg">
          <div className="overflow-x-auto rounded-lg border border-border bg-card/90 px-5 py-4 text-left font-mono text-sm shadow-[0_18px_50px_rgba(16,31,74,0.06)]">
            <span className="select-none text-muted-foreground">
              <span className="text-coral">E</span> — what we ingested ·{" "}
              <span className="text-teal">K</span> — what we concluded ·{" "}
              <span className="text-foreground">P</span> — how we reach it
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}
