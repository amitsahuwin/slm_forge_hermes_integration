import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import Nav from './components/Nav';
import Dashboard from './pages/Dashboard';
import Datasets from './pages/Datasets';
import Exports from './pages/Exports';
import Maintenance from './pages/Maintenance';
import NewDataset from './pages/NewDataset';
import NewRun from './pages/NewRun';
import NewSession from './pages/NewSession';
import RunDetail from './pages/RunDetail';
import Runs from './pages/Runs';
import SessionDetail from './pages/SessionDetail';
import Sessions from './pages/Sessions';

export default function App() {
  return (
    <BrowserRouter>
      <div className="min-h-screen bg-zinc-950 text-zinc-100">
        <Nav />
        <main className="mx-auto max-w-7xl px-8 py-10">
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/sessions" element={<Sessions />} />
            <Route path="/sessions/new" element={<NewSession />} />
            <Route path="/sessions/:id" element={<SessionDetail />} />
            <Route path="/runs" element={<Runs />} />
            <Route path="/runs/new" element={<NewRun />} />
            <Route path="/runs/:id" element={<RunDetail />} />
            <Route path="/exports" element={<Exports />} />
            <Route path="/datasets" element={<Datasets />} />
            <Route path="/datasets/new" element={<NewDataset />} />
            <Route path="/maintenance" element={<Maintenance />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  );
}
