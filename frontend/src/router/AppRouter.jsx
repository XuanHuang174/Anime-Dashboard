import { Navigate, Route, Routes } from 'react-router-dom'
import PublicHomePage from '../App.jsx'
import AdminProtectedRoute from '../components/AdminProtectedRoute.jsx'
import ProtectedRoute from '../components/ProtectedRoute.jsx'
import ActivatePage from '../pages/ActivatePage.jsx'
import AdminDashboard from '../pages/AdminDashboard.jsx'
import AdminLogin from '../pages/AdminLogin.jsx'
import Dashboard from '../pages/Dashboard.jsx'
import FeaturedColumn from '../pages/FeaturedColumn.jsx'
import LoginPage from '../pages/LoginPage.jsx'
import ResetPasswordPage from '../pages/ResetPasswordPage.jsx'

function AppRouter() {
  return (
    <Routes>
      <Route path="/" element={<PublicHomePage />} />
      <Route path="/featured" element={<FeaturedColumn />} />
      <Route path="/login" element={<LoginPage />} />
      <Route path="/activate" element={<ActivatePage />} />
      <Route path="/reset-password" element={<ResetPasswordPage />} />
      <Route path="/admin-login" element={<AdminLogin />} />
      <Route
        path="/admin-dashboard"
        element={(
          <AdminProtectedRoute>
            <AdminDashboard />
          </AdminProtectedRoute>
        )}
      />
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
