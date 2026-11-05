import { defineConfig } from 'vitepress'
import { existsSync, readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

// Sidebar and the list of runs come from publish_site.py (generated.json); an empty site still builds.
const file = fileURLToPath(new URL('./generated.json', import.meta.url))
const gen = existsSync(file) ? JSON.parse(readFileSync(file, 'utf-8')) : { runs: [], benchmarks: [] }

export default defineConfig({
  title: 'Expedition swarm',
  description: 'Living handbook: expedition plans made by a swarm of local LLM agents and checked by code.',
  // The repository is published at https://<user>.github.io/thesis-swarm/
  base: process.env.SITE_BASE ?? '/thesis-swarm/',
  lastUpdated: false,
  themeConfig: {
    nav: [
      { text: 'Plans', link: '/' },
      { text: 'About', link: '/about' },
      { text: 'Repository', link: 'https://github.com/Winp3nalty/thesis-swarm' },
    ],
    sidebar: [
      { text: 'Expedition plans', items: gen.runs },
      ...(gen.benchmarks.length ? [{ text: 'Benchmarks', items: gen.benchmarks }] : []),
      { text: 'Project', items: [{ text: 'About', link: '/about' }] },
    ],
    search: { provider: 'local' },
    outline: { level: [2, 3] },
  },
})
