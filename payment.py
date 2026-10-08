
// src/routes/AdminRoute.tsx
import { Navigate, useLocation } from "react-router-dom";
import Container from "react-bootstrap/Container";
//import Alert from "react-bootstrap/Alert";
import { useAuth } from "@/context/AuthContext";

interface AdminRouteProps {
  children: React.ReactNode;
}

const AdminRoute = ({ children }: AdminRouteProps) => {
  const { user, isLoading } = useAuth();
  const location = useLocation();

  if (isLoading) {
    return (
      <Container className="py-5 text-center">
        <p className="text-muted">Loading...</p>
      </Container>
    );
  }

  if (!user) {
    return <Navigate to="/login" replace state={{ from: location }} />;
  }

  if (!user.is_admin) {
    return <Navigate to="/" replace state={{ from: location }} />;
  }

  return <>{children}</>;
};

export default AdminRoute;





// src/routes/PermissionRoute.tsx
import { Navigate, useLocation } from "react-router-dom";
import Container from "react-bootstrap/Container";
import { useAuth } from "@/context/AuthContext";

interface PermissionRouteProps {
  permission: string;
  children: React.ReactNode;
}

const PermissionRoute = ({ permission, children }: PermissionRouteProps) => {
  const { user, isLoading } = useAuth();
  const location = useLocation();

  if (isLoading) {
    return (
      <Container className="py-5 text-center">
        <p className="text-muted">Loading...</p>
      </Container>
    );
  }

  if (!user) {
    return <Navigate to="/login" replace state={{ from: location }} />;
  }

  // Admin can access everything (optional — remove if you don't want this)
  const allowed = user.is_admin || user.permission === permission;

  if (!allowed) {
    return <Navigate to="/" replace />;
  }

  return <>{children}</>;
};

export default PermissionRoute;


//src/routes/userRoute.tsx 
// for authenticated users only
import { Navigate, useLocation } from "react-router-dom";
import Container from "react-bootstrap/Container";
import { useAuth } from "@/context/AuthContext";

interface UserRouteProps {
  children: React.ReactNode;
}

const UserRoute = ({ children }: UserRouteProps) => {
  const { user, isLoading } = useAuth();
  const location = useLocation();

  if (isLoading) {
    return (
      <Container className="py-5 text-center">
        <p className="text-muted">Loading...</p>
      </Container>
    );
  }

  if (!user) {
    return (
      <Navigate
        to="/login"
        replace
        state={{ from: location }}
      />
    );
  }

  return <>{children}</>;
};

export default UserRoute;




//src/App.tsx
import { createBrowserRouter } from "react-router-dom";
import Layout from "./base/Layout";
import Home from "./pages/Home";
import Login from "./pages/users/Login";
import NotFound from "./pages/NotFound";
import Profile from "./pages/Profile";
import CreateCountry from "./pages/countries/CreateCountry";
import CountriesList from "./pages/countries/CountriesList";
import SetupHome from "./pages/admin/SetUpHome";
import CountryDetail from "./pages/countries/CountryDetail";
import UpdateCountry from "./pages/countries/UpdateCountry";
import VerifyLogin from "./pages/users/VerifyLogin";
import RegisterVerify from "./pages/users/RegisterVerify";
import UserRoute from "./routes/UserRoute";
import AdminRoute from "./routes/AdminRoute";
import Register from "./pages/users/Register";
import UpdateName from "./pages/users/UpdateNames";
import ChangePassword from "./pages/users/ChangePassword";
import PasswordChangeConfirm from "./pages/users/ConfirmPasswordchange";
import ApproveEmailChange from "./pages/users/ApproveEmailChange";
import RequestEmailChange from "./pages/users/RequestEmailChange";
import VerifyNewEmailChange from "./pages/users/VerifyNewEmailChange";
import PasswordResetRequest from "./pages/users/PasswordResetRequest";
import ResetPasswordVerify from "./pages/users/ResetPasswordVerify";





export const router = createBrowserRouter([

  {
    element: <Layout />,
    children: [
      // PUBLIC
      { path: '/', element: <Home />},
      { path: '/login', element: <Login />},
      { path: '/countries', element: <CountriesList />},
      { path: '/register', element: <Register />},

      { path: '/login/verify', element: <VerifyLogin />},
     
      { path: '/register/verify', element: <RegisterVerify />},
      // LOGGED IN USERS
      { path: '/profile', 
        element: (
          <UserRoute>
            <Profile />
          </UserRoute>
        ),
      },
      { path: '/change/names', 
        element: (
          <UserRoute>
            <UpdateName />
          </UserRoute>
        ),
      },
      { path: '/change/password', 
        element: (
          <UserRoute>
            <ChangePassword />
          </UserRoute>
        ),
      },
      { path: '/confirm/password/change', 
        element: (
          <UserRoute>
            <PasswordChangeConfirm />
          </UserRoute>
        ),
      },
      { path: '/email/approval', 
        element: (
          <UserRoute>
            <ApproveEmailChange />
          </UserRoute>
        ),
      },
      { path: '/email/change', 
        element: (
          <UserRoute>
            <RequestEmailChange />
          </UserRoute>
        ),
      },
      { path: '/email/verify', 
        element: (
          <UserRoute>
            <VerifyNewEmailChange />
          </UserRoute>
        ),
      },
      { path: '/password/reset/request', 
        element: (
          <UserRoute>
            <PasswordResetRequest />
          </UserRoute>
        ),
      },
      { path: '/password/reset/verify', 
        element: (
          <UserRoute>
            <ResetPasswordVerify />
          </UserRoute>
        ),
      },

      // ADMIN
      { path: '/add_country', 
        element: (
          <AdminRoute>
            <CreateCountry />
          </AdminRoute>
        ),
      },


      { path: '/add_home', 
        element: (
          <AdminRoute>
            <SetupHome />
          </AdminRoute>
        ),
      },


      { path: '/add_country', 
        element: (
          <AdminRoute>
            <CreateCountry />
          </AdminRoute>
        ),
      },

      { path: '/country/:slug', 
        element: (
          <AdminRoute>
            <CountryDetail />
          </AdminRoute>
        ),
      },

      { path: '/country/:slug/edit', 
        element: (
          <AdminRoute>
            <UpdateCountry />
          </AdminRoute>
        ),
      },
      
    
      
      

      // 404 route must be the last
      {path: "*", element: <NotFound />},
    ],
  },
 

]);









