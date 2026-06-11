import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import Nav from './components/Nav';
import Agents from './pages/Agents';
import Chat from './pages/Chat';
import Dashboard from './pages/Dashboard';
import DatasetDetail from './pages/DatasetDetail';
import Datasets from './pages/Datasets';
import Research from './pages/Research';
import ExperimentDetail from './pages/ExperimentDetail';
import Experiments from './pages/Experiments';
import Exports from './pages/Exports';
import Maintenance from './pages/Maintenance';
import NewDatasetV2 from './pages/NewDatasetV2';
import NewExperiment from './pages/NewExperiment';
import NewRun from './pages/NewRun';
import RunDetail from './pages/RunDetail';
import Runs from './pages/Runs';
import AdminUsers from './pages/AdminUsers';
import { AuthProvider } from './auth/AuthContext';
import Callback from './auth/Callback';
import { ToastContainer } from './components/Toast';

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <div className="min-h-screen bg-zinc-950 text-zinc-100">
          <Nav />
          <main className="mx-auto max-w-7xl px-8 py-10">
            <Routes>
              <Route path="/" element={<Dashboard />} />

              {/* Experiments (new canonical URLs) */}
              <Route path="/experiments" element={<Experiments />} />
              <Route path="/experiments/new" element={<NewExperiment />} />
              <Route path="/experiments/:id" element={<ExperimentDetail />} />

              {/* Legacy /sessions URLs redirect to /experiments for bookmark compatibility */}
              <Route path="/sessions" element={<Navigate to="/experiments" replace />} />
              <Route path="/sessions/new" element={<Navigate to="/experiments/new" replace />} />
              <Route path="/sessions/:id" element={<LegacySessionRedirect />} />

              <Route path="/runs" element={<Runs />} />
              <Route path="/runs/new" element={<NewRun />} />
              <Route path="/runs/:id" element={<RunDetail />} />
              <Route path="/exports" element={<Exports />} />
              <Route path="/datasets" element={<Datasets />} />
              <Route path="/datasets/new" element={<NewDatasetV2 />} />
              <Route path="/datasets/:name" element={<DatasetDetail />} />
              <Route path="/maintenance" element={<Maintenance />} />
              <Route path="/chat" element={<Chat />} />
              <Route path="/chat/:cid" element={<Chat />} />
              <Route path="/research" element={<Research />} />
              <Route path="/agents" element={<Agents />} />
              <Route path="/auth/callback" element={<Callback />} />
              <Route path="/admin/users" element={<AdminUsers />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </main>
          <ToastContainer />
        </div>
      </BrowserRouter>
    </AuthProvider>
  );
}

/** Redirects /sessions/:id → /experiments/:id, preserving the id. */
function LegacySessionRedirect() {
  // useParams isn't easily accessible without making this a small component
  const id = window.location.pathname.split('/').pop();
  return <Navigate to={`/experiments/${id}`} replace />;
}
