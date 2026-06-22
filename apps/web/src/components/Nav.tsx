import { NavLink } from 'react-router-dom';
import UserBadge from './UserBadge';
import PlatformBadge from './PlatformBadge';
import { useCan, useCanSeeNav } from '../auth/useCan';

// Compact link styling — narrower padding + smaller text so 9+ tabs fit
// alongside the action buttons + user badge on a 1440 viewport without
// wrapping to a second line. `shrink-0` on the tab strip prevents the
// flex container from compressing the labels into ellipses.
const link =
  'rounded-md px-2 py-1 text-[13px] font-medium text-zinc-400 transition-colors hover:bg-zinc-800/70 hover:text-zinc-100 whitespace-nowrap';
const activeLink = 'bg-zinc-800 text-zinc-100';

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
    <header className="border-b border-zinc-800">
      <div className="mx-auto flex max-w-[1600px] items-center gap-4 px-6 py-3">
        <NavLink
          to="/"
          className="shrink-0 whitespace-nowrap text-base font-semibold tracking-tight"
        >
          SLM-Forge
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
              className="whitespace-nowrap rounded-md border border-zinc-800 px-2.5 py-1 text-[12px] font-medium text-zinc-300 hover:border-zinc-700 hover:bg-zinc-900"
            >
              + Dataset
            </NavLink>
          )}
          {canCreateExperiment && (
            <NavLink
              to="/experiments/new"
              className="whitespace-nowrap rounded-md bg-emerald-600 px-2.5 py-1 text-[12px] font-medium text-white hover:bg-emerald-500"
            >
              + Experiment
            </NavLink>
          )}
          <PlatformBadge />
          <div className="ml-1 border-l border-zinc-800 pl-2">
            <UserBadge />
          </div>
        </div>
      </div>
    </header>
  );
}
