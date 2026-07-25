import { Navigate, Route, Routes } from 'react-router-dom'
import PublicHomePage from '../App.jsx'
import ProtectedRoute from '../components/ProtectedRoute.jsx'
import ActivatePage from '../pages/ActivatePage.jsx'
import Dashboard from '../pages/Dashboard.jsx'
import LoginPage from '../pages/LoginPage.jsx'
import ResetPasswordPage from '../pages/ResetPasswordPage.jsx'

function AppRouter() {
  return (
    <Routes>
      <Route path="/" element={<PublicHomePage />} />
      <Route path="/login" element={<LoginPage />} />
      <Route path="/activate" element={<ActivatePage />} />
      <Route path="/reset-password" element={<ResetPasswordPage />} />
      <Route
        path="/dashboard"
        element={(
          <ProtectedRoute>
            <Dashboard />
          </ProtectedRoute>
        )}
      />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}

export default AppRouter
