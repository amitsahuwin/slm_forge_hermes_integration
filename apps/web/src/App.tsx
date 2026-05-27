import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import Nav from './components/Nav';
import Dashboard from './pages/Dashboard';
import Datasets from './pages/Datasets';
import NewRun from './pages/NewRun';
import RunDetail from './pages/RunDetail';
import Runs from './pages/Runs';

export default function App() {
  return (
    <BrowserRouter>
      <div className="min-h-screen bg-zinc-950 text-zinc-100">
        <Nav />
        <main className="mx-auto max-w-6xl px-8 py-10">
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/runs" element={<Runs />} />
            <Route path="/runs/new" element={<NewRun />} />
            <Route path="/runs/:id" element={<RunDetail />} />
            <Route path="/datasets" element={<Datasets />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  );
}
