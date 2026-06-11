import { NavLink } from 'react-router-dom';
import UserBadge from './UserBadge';
import { useCan, useCanSeeNav } from '../auth/useCan';

const link =
  'rounded-md px-3 py-1.5 text-sm font-medium text-zinc-400 transition-colors hover:bg-zinc-800/70 hover:text-zinc-100';
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
  };
  // Header action buttons (right side).
  const canCreateDataset = useCan('create', 'dataset');
  const canCreateExperiment = useCan('create', 'experiment');

  return (
    <header className="border-b border-zinc-800">
      <div className="mx-auto flex max-w-7xl items-center justify-between px-8 py-4">
        <div className="flex items-center gap-8">
          <NavLink to="/" className="text-lg font-semibold tracking-tight">
            SLM-Forge
          </NavLink>
          <nav className="flex items-center gap-1">
            <NavLink to="/" end className={({ isActive }) => `${link} ${isActive ? activeLink : ''}`}>
              Dashboard
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
          </nav>
        </div>
        <div className="flex items-center gap-2">
          {canCreateDataset && (
            <NavLink
              to="/datasets/new"
              className="rounded-md border border-zinc-800 px-3 py-1.5 text-sm font-medium text-zinc-300 hover:border-zinc-700 hover:bg-zinc-900"
            >
              + Dataset
            </NavLink>
          )}
          {canCreateExperiment && (
            <NavLink
              to="/experiments/new"
              className="rounded-md bg-emerald-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-emerald-500"
            >
              + Experiment
            </NavLink>
          )}
          <div className="ml-2 border-l border-zinc-800 pl-3">
            <UserBadge />
          </div>
        </div>
      </div>
    </header>
  );
}
