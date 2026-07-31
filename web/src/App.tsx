import { Navigate, Route, Routes } from 'react-router'
import CaseListPage from './pages/CaseListPage'
import CaseViewPage from './pages/CaseViewPage'
import BuildFooter from './components/BuildFooter'

export default function App() {
  return (
    <div className="min-h-screen flex flex-col bg-slate-50 text-slate-900">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto max-w-6xl px-4 py-3">
          <h1 className="text-lg font-semibold">Medical Case Generator</h1>
        </div>
      </header>

      <main className="flex-1 mx-auto w-full max-w-6xl px-4 py-6">
        <Routes>
          <Route path="/" element={<Navigate to="/cases" replace />} />
          <Route path="/cases" element={<CaseListPage />} />
          <Route path="/cases/:caseId" element={<CaseViewPage />} />
          <Route path="*" element={<p className="text-slate-500">Not found.</p>} />
        </Routes>
      </main>

      <BuildFooter />
    </div>
  )
}
