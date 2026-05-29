import { NavLink } from 'react-router-dom';

const link =
  'rounded-md px-3 py-1.5 text-sm font-medium text-zinc-400 transition-colors hover:bg-zinc-800/70 hover:text-zinc-100';
const activeLink = 'bg-zinc-800 text-zinc-100';

export default function Nav() {
  return (
    <header className="border-b border-zinc-800">
      <div className="mx-auto flex max-w-7xl items-center justify-between px-8 py-4">
        <div className="flex items-center gap-8">
          <NavLink to="/" className="text-lg font-semibold tracking-tight">
            SLM-Forge
          </NavLink>
          <nav className="flex items-center gap-1">
            <NavLink to="/" end className={({ isActive }) => `${link} ${isActive ? activeLink : ''}`}>Dashboard</NavLink>
            <NavLink to="/sessions" className={({ isActive }) => `${link} ${isActive ? activeLink : ''}`}>Sessions</NavLink>
            <NavLink to="/runs" className={({ isActive }) => `${link} ${isActive ? activeLink : ''}`}>Runs</NavLink>
            <NavLink to="/exports" className={({ isActive }) => `${link} ${isActive ? activeLink : ''}`}>Exports</NavLink>
            <NavLink to="/datasets" className={({ isActive }) => `${link} ${isActive ? activeLink : ''}`}>Datasets</NavLink>
          </nav>
        </div>
        <div className="flex items-center gap-2">
          <NavLink to="/datasets/new" className="rounded-md border border-zinc-800 px-3 py-1.5 text-sm font-medium text-zinc-300 hover:border-zinc-700 hover:bg-zinc-900">+ Dataset</NavLink>
          <NavLink to="/sessions/new" className="rounded-md bg-emerald-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-emerald-500">+ Session</NavLink>
        </div>
      </div>
    </header>
  );
}
