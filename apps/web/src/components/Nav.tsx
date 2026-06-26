import { NavLink } from 'react-router-dom';
import UserBadge from './UserBadge';
import PlatformBadge from './PlatformBadge';
import { useCan, useCanSeeNav } from '../auth/useCan';

// Compact link styling — narrower padding + smaller text so 9+ tabs fit
// alongside the action buttons + user badge on a 1440 viewport without
// wrapping to a second line. `shrink-0` on the tab strip prevents the
// flex container from compressing the labels into ellipses.
const link =
  'rounded-md px-2.5 py-1 text-[13px] font-semibold text-white nav-text-shadow transition-colors hover:bg-white/15 whitespace-nowrap';
const activeLink = 'bg-white/25 text-white';

export default function Nav() {
  // Top-level nav: each tab is conditionally rendered based on the user's
  // permissions. Dashboard is always visible.
  const show = {
    experiments: useCanSeeNav('experiments'),
    runs: useCanSeeNav('runs'),
    exports: useCanSeeNav('exports'),
    datasets: useCanSeeNav('datasets'),
    maintenance: useCanSeeNav('maintenance'),
    chat: useCanSeeNav('chat'),
    research: useCanSeeNav('research'),
    agents: useCanSeeNav('agents'),
    traces: useCanSeeNav('traces'),
    autofix: useCanSeeNav('autofix'),
  };
  // Header action buttons (right side).
  const canCreateDataset = useCan('create', 'dataset');
  const canCreateExperiment = useCan('create', 'experiment');

  return (
    <header style={{ background: 'linear-gradient(135deg, #0B1D3A 0%, #0E3A5C 30%, #17707F 65%, #1A8A8F 100%)' }} className="shadow-md">
      <div className="mx-auto flex max-w-[1600px] items-center gap-4 px-6 py-3">
        <NavLink
          to="/"
          className="shrink-0 whitespace-nowrap text-base font-bold tracking-tight text-white"
        >
          <span className="text-white">SLM-Forge</span>
        </NavLink>
        <nav className="flex flex-1 items-center gap-0.5 overflow-x-auto">
            <NavLink to="/" end className={({ isActive }) => `${link} ${isActive ? activeLink : ''}`}>
              Dashboard
            </NavLink>
            <NavLink to="/product" className={({ isActive }) => `${link} ${isActive ? activeLink : ''}`}>
              Product
            </NavLink>
            {show.experiments && (
              <NavLink to="/experiments" className={({ isActive }) => `${link} ${isActive ? activeLink : ''}`}>
                Experiments
              </NavLink>
            )}
            {show.runs && (
              <NavLink to="/runs" className={({ isActive }) => `${link} ${isActive ? activeLink : ''}`}>
                Runs
              </NavLink>
            )}
            {show.exports && (
              <NavLink to="/exports" className={({ isActive }) => `${link} ${isActive ? activeLink : ''}`}>
                Exports
              </NavLink>
            )}
            {show.datasets && (
              <NavLink to="/datasets" className={({ isActive }) => `${link} ${isActive ? activeLink : ''}`}>
                Datasets
              </NavLink>
            )}
            {show.maintenance && (
              <NavLink to="/maintenance" className={({ isActive }) => `${link} ${isActive ? activeLink : ''}`}>
                Maintenance
              </NavLink>
            )}
            {show.chat && (
              <NavLink to="/chat" className={({ isActive }) => `${link} ${isActive ? activeLink : ''}`}>
                Chat
              </NavLink>
            )}
            {show.research && (
              <NavLink to="/research" className={({ isActive }) => `${link} ${isActive ? activeLink : ''}`}>
                R&D
              </NavLink>
            )}
            {show.agents && (
              <NavLink to="/agents" className={({ isActive }) => `${link} ${isActive ? activeLink : ''}`}>
                Agents
              </NavLink>
            )}
            {show.traces && (
              <NavLink to="/traces" className={({ isActive }) => `${link} ${isActive ? activeLink : ''}`}>
                Traces
              </NavLink>
            )}
            {show.autofix && (
              <NavLink to="/autofix" className={({ isActive }) => `${link} ${isActive ? activeLink : ''}`}>
                Auto-Fixes
              </NavLink>
            )}
        </nav>
        <div className="flex shrink-0 items-center gap-1.5">
          {canCreateDataset && (
            <NavLink
              to="/datasets/new"
              className="whitespace-nowrap rounded-md border border-white/30 px-2.5 py-1 text-[12px] font-medium text-white/90 hover:border-white/50 hover:bg-white/10"
            >
              + Dataset
            </NavLink>
          )}
          {canCreateExperiment && (
            <NavLink
              to="/experiments/new"
              className="whitespace-nowrap rounded-md bg-white px-2.5 py-1 text-[12px] font-bold text-hcl-dark-teal hover:bg-hcl-light-blue"
            >
              + Experiment
            </NavLink>
          )}
          <PlatformBadge />
          <div className="ml-1 border-l border-white/30 pl-2">
            <UserBadge />
          </div>
        </div>
      </div>
    </header>
  );
}
