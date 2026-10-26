import React from "react";
import ReactDOM from "react-dom/client";
import { createBrowserRouter, RouterProvider } from "react-router-dom";
import App from "./App";
import RouteError from "./components/RouteError";
import "./styles.css";

// A data router (not <BrowserRouter>) so the unsaved-changes guard can use
// useBlocker; App keeps its own <Routes> under this one catch-all route.
const router = createBrowserRouter([{ path: "*", element: <App />, errorElement: <RouteError /> }]);

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <RouterProvider router={router} />
  </React.StrictMode>,
);
