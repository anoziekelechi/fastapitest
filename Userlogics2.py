// src/types/payment/read.ts

export interface PaymentMethodRead {
  id: number;
  name: string;
  slug?: string | null;
  payment_logo_url?: string | null;
  created_at: string;
  updated_at: string;
}

export interface AvailablePaymentMethodsRead {
  total: number;
  methods: PaymentMethodRead[];
}


// src/types/payment/create.ts

import type { PaymentMethodRead } from "./read";

export interface PaymentMethodCreate {
  name: string;
}

export interface CreatePaymentMethodResponse {
  message: string;
  payment_method: PaymentMethodRead;
}


// src/types/payment/update.ts

import type { PaymentMethodRead } from "./read";

export interface PaymentMethodUpdate {
  name?: string;
}

export interface UpdatePaymentMethodResponse {
  message: string;
  payment_method: PaymentMethodRead;
}


// src/types/payment/delete.ts

export interface DeletePaymentMethodResponse {
  message: string;
}


// src/types/payment/index.ts

export * from "./read";
export * from "./create";
export * from "./update";
export * from "./delete";




// src/pages/payments/PaymentMethodsList.tsx

import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import axios from "axios";

import Container from "react-bootstrap/Container";
import ListGroup from "react-bootstrap/ListGroup";
import Spinner from "react-bootstrap/Spinner";
import Alert from "react-bootstrap/Alert";

import api from "@/api/client";
import { handleApiError } from "@/lib/handleApiError";
import type { AvailablePaymentMethodsRead } from "@/types";


const PaymentMethodsList = () => {
  const navigate = useNavigate();

  const [data, setData] = useState<AvailablePaymentMethodsRead | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    let mounted = true;

    api
      .get<AvailablePaymentMethodsRead>("/payment-methods", {
        signal: controller.signal,
      })
      .then((res) => {
        if (mounted) setData(res.data);
      })
      .catch((err) => {
        if (axios.isCancel(err)) return;
        if (!mounted) return;
        const msg = handleApiError(err, { navigate });
        if (msg) setError(msg);
      })
      .finally(() => {
        if (mounted && !controller.signal.aborted) setLoading(false);
      });

    return () => {
      mounted = false;
      controller.abort();
    };
  }, [navigate]);

  if (loading) {
    return (
      <Container className="py-5 text-center">
        <Spinner animation="border" role="status" />
        <p className="mt-3 text-muted">Loading payment methods...</p>
      </Container>
    );
  }

  if (error) {
    return (
      <Container className="py-5" style={{ maxWidth: 720 }}>
        <Alert variant="danger" className="text-center">
          {error}
        </Alert>
      </Container>
    );
  }

  const methods = data?.methods ?? [];

  return (
    <Container className="py-4" style={{ maxWidth: 720 }}>
      <div className="d-flex justify-content-between align-items-center mb-4">
        <h1 className="h3 mb-0">Payment Methods</h1>
        <Link to="/payment-methods/new" className="btn btn-primary">
          + Add Payment Method
        </Link>
      </div>

      {methods.length === 0 ? (
        <p className="text-center text-muted py-5">
          No payment methods yet.
        </p>
      ) : (
        <ListGroup>
          {methods.map((method) => (
            <ListGroup.Item
              key={method.id}
              action
              as={Link}
              to={`/payment-methods/${method.slug}`}
              className="d-flex justify-content-between align-items-center"
            >
              <div className="d-flex align-items-center gap-3">
                {method.payment_logo_url ? (
                  <img
                    src={method.payment_logo_url}
                    alt={method.name}
                    style={{
                      height: 28,
                      width: "auto",
                      maxWidth: 80,
                      objectFit: "contain",
                    }}
                  />
                ) : (
                  <span
                    className="badge bg-light text-dark border"
                    style={{ fontSize: "0.7rem" }}
                  >
                    NO LOGO
                  </span>
                )}
                <span className="fw-medium">{method.name}</span>
              </div>

              <span className="text-muted small">
                {method.slug ?? "—"}
              </span>
            </ListGroup.Item>
          ))}
        </ListGroup>
      )}
    </Container>
  );
};

export default PaymentMethodsList;


// src/components/payments/DeletePaymentMethodModal.tsx

import Modal from "react-bootstrap/Modal";
import Button from "react-bootstrap/Button";
import Spinner from "react-bootstrap/Spinner";
import Alert from "react-bootstrap/Alert";

import type { PaymentMethodRead } from "@/types";

interface DeletePaymentMethodModalProps {
  method: PaymentMethodRead | null;
  deleting: boolean;
  serverError?: string | null;
  onConfirm: () => void;
  onCancel: () => void;
}

const DeletePaymentMethodModal = ({
  method,
  deleting,
  serverError,
  onConfirm,
  onCancel,
}: DeletePaymentMethodModalProps) => {
  const isOpen = method !== null;

  return (
    <Modal
      show={isOpen}
      onHide={deleting ? undefined : onCancel}
      backdrop={deleting ? "static" : true}
      keyboard={!deleting}
      centered
      aria-labelledby="delete-payment-method-title"
    >
      <Modal.Header closeButton={!deleting}>
        <Modal.Title id="delete-payment-method-title">
          Delete Payment Method
        </Modal.Title>
      </Modal.Header>

      <Modal.Body>
        {serverError && (
          <Alert variant="danger" className="py-2 small">
            {serverError}
          </Alert>
        )}

        <p className="mb-3">
          You are about to delete{" "}
          <strong>{method?.name ?? "this payment method"}</strong>.
        </p>

        <Alert variant="warning" className="mb-3">
          Any receipts referencing this payment method will lose
          their reference. This action cannot be undone.
        </Alert>

        <p className="mb-0 text-muted small">
          Do you still want to continue?
        </p>
      </Modal.Body>

      <Modal.Footer>
        <Button
          variant="outline-secondary"
          onClick={onCancel}
          disabled={deleting}
        >
          No, take me back
        </Button>
        <Button
          variant="danger"
          onClick={onConfirm}
          disabled={deleting}
        >
          {deleting ? (
            <>
              <Spinner
                as="span"
                animation="border"
                size="sm"
                role="status"
                aria-hidden="true"
                className="me-2"
              />
              Deleting...
            </>
          ) : (
            "Yes, delete it"
          )}
        </Button>
      </Modal.Footer>
    </Modal>
  );
};

export default DeletePaymentMethodModal;



// src/pages/payments/PaymentMethodDetail.tsx

import { useEffect, useState } from "react";
import { useParams, useNavigate, Link } from "react-router-dom";
import axios from "axios";

import Container from "react-bootstrap/Container";
import ListGroup from "react-bootstrap/ListGroup";
import Button from "react-bootstrap/Button";
import Spinner from "react-bootstrap/Spinner";
import Alert from "react-bootstrap/Alert";

import api from "@/api/client";
import { handleApiError } from "@/lib/handleApiError";
import DeletePaymentMethodModal from "@/components/payments/DeletePaymentMethodModal";
import type {
  DeletePaymentMethodResponse,
  PaymentMethodRead,
} from "@/types";


const PaymentMethodDetail = () => {
  const { slug } = useParams<{ slug: string }>();
  const navigate = useNavigate();

  const [method, setMethod] = useState<PaymentMethodRead | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const [showDeleteModal, setShowDeleteModal] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  useEffect(() => {
    if (!slug) return;

    const controller = new AbortController();
    let mounted = true;

    api
      .get<PaymentMethodRead>(`/payment-methods/${slug}`, {
        signal: controller.signal,
      })
      .then((res) => {
        if (mounted) setMethod(res.data);
      })
      .catch((err) => {
        if (axios.isCancel(err)) return;
        if (!mounted) return;
        const msg = handleApiError(err, { navigate });
        if (msg) setError(msg);
      })
      .finally(() => {
        if (mounted && !controller.signal.aborted) setLoading(false);
      });

    return () => {
      mounted = false;
      controller.abort();
    };
  }, [slug, navigate]);


  const openDeleteModal = () => {
    setDeleteError(null);
    setShowDeleteModal(true);
  };

  const closeDeleteModal = () => {
    if (deleting) return;
    setShowDeleteModal(false);
    setDeleteError(null);
  };

  const confirmDelete = async () => {
    if (!method) return;

    setDeleting(true);
    setDeleteError(null);

    try {
      const res = await api.delete<DeletePaymentMethodResponse>(
        `/payment-methods/${slug}`
      );
      setShowDeleteModal(false);
      setMessage(res.data.message);
      window.setTimeout(() => navigate("/payment-methods"), 1800);
    } catch (err) {
      const msg = handleApiError(err, {
        navigate,
        redirectOnAuth: false,
        redirectOnForbidden: false,
      });
      if (msg) setDeleteError(msg);
    } finally {
      setDeleting(false);
    }
  };


  if (loading) {
    return (
      <Container className="py-5 text-center">
        <Spinner animation="border" />
      </Container>
    );
  }

  if (error && !method) {
    return (
      <Container className="py-5" style={{ maxWidth: 720 }}>
        <Alert variant="danger">{error}</Alert>
        <Link to="/payment-methods">← Back to Payment Methods</Link>
      </Container>
    );
  }

  if (!method) return null;

  return (
    <Container className="py-4" style={{ maxWidth: 720 }}>
      {message && (
        <Alert variant="success" className="mb-4">
          {message}
        </Alert>
      )}
      {error && (
        <Alert variant="danger" className="mb-4">
          {error}
        </Alert>
      )}

      <div className="d-flex justify-content-between align-items-start mb-4">
        <h1 className="h3 mb-0">{method.name}</h1>
        <div className="d-flex gap-2">
          <Button
            as={Link as any}
            to={`/payment-methods/${method.slug}/edit`}
            variant="warning"
          >
            Update
          </Button>
          <Button variant="danger" onClick={openDeleteModal}>
            Delete
          </Button>
        </div>
      </div>

      {method.payment_logo_url && (
        <div className="mb-4 text-center">
          <img
            src={method.payment_logo_url}
            alt={`${method.name} logo`}
            style={{
              height: 64,
              maxWidth: 200,
              objectFit: "contain",
            }}
          />
        </div>
      )}

      <ListGroup>
        <ListGroup.Item className="d-flex justify-content-between">
          <strong>Slug</strong>
          <span>{method.slug ?? "—"}</span>
        </ListGroup.Item>
        <ListGroup.Item className="d-flex justify-content-between">
          <strong>Logo</strong>
          <span>{method.payment_logo_url ? "Provided" : "Not set"}</span>
        </ListGroup.Item>
        <ListGroup.Item className="d-flex justify-content-between">
          <strong>Created</strong>
          <span>{new Date(method.created_at).toLocaleString()}</span>
        </ListGroup.Item>
        <ListGroup.Item className="d-flex justify-content-between">
          <strong>Updated</strong>
          <span>{new Date(method.updated_at).toLocaleString()}</span>
        </ListGroup.Item>
      </ListGroup>

      <Link to="/payment-methods" className="d-inline-block mt-4">
        ← Back to Payment Methods
      </Link>

      <DeletePaymentMethodModal
        method={showDeleteModal ? method : null}
        deleting={deleting}
        serverError={deleteError}
        onConfirm={confirmDelete}
        onCancel={closeDeleteModal}
      />
    </Container>
  );
};

export default PaymentMethodDetail;



// src/pages/payments/CreatePaymentMethod.tsx

import { useEffect, useRef, useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useNavigate, Navigate, Link } from "react-router-dom";

import Form from "react-bootstrap/Form";
import Button from "react-bootstrap/Button";
import Container from "react-bootstrap/Container";
import Alert from "react-bootstrap/Alert";
import Spinner from "react-bootstrap/Spinner";

import api from "@/api/client";
import { useAuth } from "@/context/AuthContext";
import { handleApiError } from "@/lib/handleApiError";
import type { CreatePaymentMethodResponse } from "@/types";


// =============================================================
// CONSTANTS
// =============================================================

const MAX_LOGO_BYTES = 2 * 1024 * 1024;   // 2 MiB
const ALLOWED_TYPES = [
  "image/png",
  "image/jpeg",
  "image/webp",
  "image/svg+xml",
];


// =============================================================
// VALIDATION
// =============================================================

const schema = z.object({
  name: z
    .string()
    .trim()
    .min(2, "Payment method name must be at least 2 characters")
    .max(100, "Payment method name must not exceed 100 characters"),

  payment_logo: z.any().optional(),
});

type FormData = z.infer<typeof schema>;


// =============================================================
// COMPONENT
// =============================================================

const CreatePaymentMethod = () => {
  const navigate = useNavigate();
  const { user, isLoading: authLoading } = useAuth();

  const [serverError, setServerError] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);
  const redirectRef = useRef<{ path: string; delayMs: number } | null>(null);

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
    setError,
  } = useForm<FormData>({
    resolver: zodResolver(schema),
    defaultValues: { name: "" },
  });


  useEffect(() => {
    const p = redirectRef.current;
    if (!p) return;
    const id = window.setTimeout(
      () => navigate(p.path, { replace: true }),
      p.delayMs
    );
    return () => window.clearTimeout(id);
  }, [successMessage, navigate]);


  if (authLoading) {
    return (
      <Container className="py-5 text-center">
        <Spinner animation="border" />
      </Container>
    );
  }
  if (!user) return <Navigate to="/login" replace />;
  if (!user.is_admin) return <Navigate to="/" replace />;


  const onSubmit = async (data: FormData) => {
    setServerError(null);
    setSuccessMessage(null);
    redirectRef.current = null;

    // Client-side logo validation
    const logoFile: File | undefined =
      data.payment_logo instanceof FileList &&
      data.payment_logo.length > 0
        ? data.payment_logo[0]
        : undefined;

    if (logoFile) {
      if (!ALLOWED_TYPES.includes(logoFile.type)) {
        setError("payment_logo", {
          type: "manual",
          message: "Logo must be PNG, JPEG, WEBP, or SVG",
        });
        return;
      }
      if (logoFile.size > MAX_LOGO_BYTES) {
        setError("payment_logo", {
          type: "manual",
          message: "Logo must be under 2 MiB",
        });
        return;
      }
    }

    // Build multipart payload
    const formData = new FormData();
    formData.append("name", data.name.trim());
    if (logoFile) {
      formData.append("payment_logo", logoFile);
    }

    try {
      const res = await api.post<CreatePaymentMethodResponse>(
        "/payment-methods",
        formData
        // Do NOT set Content-Type manually — the browser sets the
        // multipart boundary itself.
      );

      setSuccessMessage(res.data.message);
      redirectRef.current = {
        path: `/payment-methods/${res.data.payment_method.slug}`,
        delayMs: 1500,
      };
    } catch (err) {
      const msg = handleApiError<FormData>(err, {
        navigate,
        setError,
        validFields: ["name", "payment_logo"],
        redirectOnAuth: false,
        redirectOnForbidden: false,
      });
      if (msg) setServerError(msg);
    }
  };


  return (
    <Container className="py-5" style={{ maxWidth: 520 }}>
      <div className="bg-white p-4 rounded shadow-sm">
        <h1 className="h3 text-center mb-4">Add Payment Method</h1>

        {successMessage && (
          <Alert variant="success" className="text-center">
            {successMessage}
            <div className="small mt-1">Redirecting...</div>
          </Alert>
        )}
        {serverError && (
          <Alert variant="danger" className="text-center">
            {serverError}
          </Alert>
        )}

        <Form onSubmit={handleSubmit(onSubmit)} noValidate>
          <Form.Group className="mb-3" controlId="name">
            <Form.Label>Payment Method Name</Form.Label>
            <Form.Control
              type="text"
              placeholder="e.g. MOMO LIBERIA"
              isInvalid={!!errors.name}
              disabled={isSubmitting || successMessage !== null}
              {...register("name")}
            />
            <Form.Control.Feedback type="invalid">
              {errors.name?.message}
            </Form.Control.Feedback>
          </Form.Group>

          <Form.Group className="mb-4" controlId="payment_logo">
            <Form.Label>
              Logo{" "}
              <span className="text-muted small">(optional)</span>
            </Form.Label>
            <Form.Control
              type="file"
              accept="image/png,image/jpeg,image/webp,image/svg+xml"
              isInvalid={!!errors.payment_logo}
              disabled={isSubmitting || successMessage !== null}
              {...register("payment_logo")}
            />
            <Form.Control.Feedback type="invalid">
              {errors.payment_logo?.message as string | undefined}
            </Form.Control.Feedback>
            <Form.Text className="text-muted">
              PNG, JPEG, WEBP, or SVG. Maximum 2 MiB.
            </Form.Text>
          </Form.Group>

          <Button
            type="submit"
            variant="primary"
            className="w-100"
            disabled={isSubmitting || successMessage !== null}
          >
            {isSubmitting ? (
              <>
                <Spinner
                  as="span"
                  animation="border"
                  size="sm"
                  role="status"
                  aria-hidden="true"
                  className="me-2"
                />
                Creating...
              </>
            ) : (
              "Create Payment Method"
            )}
          </Button>
        </Form>

        <p className="text-center mt-4 mb-0 small">
          <Link to="/payment-methods">← Back to Payment Methods</Link>
        </p>
      </div>
    </Container>
  );
};

export default CreatePaymentMethod;


// src/pages/payments/UpdatePaymentMethod.tsx

import { useEffect, useRef, useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useParams, useNavigate, Navigate, Link } from "react-router-dom";
import axios from "axios";

import Form from "react-bootstrap/Form";
import Button from "react-bootstrap/Button";
import Container from "react-bootstrap/Container";
import Alert from "react-bootstrap/Alert";
import Spinner from "react-bootstrap/Spinner";

import api from "@/api/client";
import { useAuth } from "@/context/AuthContext";
import { handleApiError } from "@/lib/handleApiError";
import type {
  PaymentMethodRead,
  UpdatePaymentMethodResponse,
} from "@/types";


// =============================================================
// CONSTANTS
// =============================================================

const MAX_LOGO_BYTES = 2 * 1024 * 1024;
const ALLOWED_TYPES = [
  "image/png",
  "image/jpeg",
  "image/webp",
  "image/svg+xml",
];


// =============================================================
// VALIDATION
// =============================================================

const schema = z.object({
  name: z
    .string()
    .trim()
    .min(2, "Payment method name must be at least 2 characters")
    .max(100, "Payment method name must not exceed 100 characters"),

  payment_logo: z.any().optional(),
});

type FormData = z.infer<typeof schema>;


// =============================================================
// COMPONENT
// =============================================================

const UpdatePaymentMethod = () => {
  const { slug } = useParams<{ slug: string }>();
  const navigate = useNavigate();
  const { user, isLoading: authLoading } = useAuth();

  const [loading, setLoading] = useState(true);
  const [currentLogoUrl, setCurrentLogoUrl] = useState<string | null>(null);
  const [serverError, setServerError] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);
  const redirectRef = useRef<{ path: string; delayMs: number } | null>(null);

  const originalNameRef = useRef<string>("");

  const {
    register,
    handleSubmit,
    reset,
    formState: { errors, isSubmitting },
    setError,
  } = useForm<FormData>({
    resolver: zodResolver(schema),
    defaultValues: { name: "" },
  });


  // Load existing method
  useEffect(() => {
    if (!slug) return;

    const controller = new AbortController();
    let mounted = true;

    api
      .get<PaymentMethodRead>(`/payment-methods/${slug}`, {
        signal: controller.signal,
      })
      .then((res) => {
        if (!mounted) return;
        originalNameRef.current = res.data.name;
        setCurrentLogoUrl(res.data.payment_logo_url ?? null);
        reset({ name: res.data.name });
      })
      .catch((err) => {
        if (axios.isCancel(err)) return;
        if (!mounted) return;
        const msg = handleApiError(err, { navigate });
        if (msg) setServerError(msg);
      })
      .finally(() => {
        if (mounted && !controller.signal.aborted) setLoading(false);
      });

    return () => {
      mounted = false;
      controller.abort();
    };
  }, [slug, navigate, reset]);


  useEffect(() => {
    const p = redirectRef.current;
    if (!p) return;
    const id = window.setTimeout(
      () => navigate(p.path, { replace: true }),
      p.delayMs
    );
    return () => window.clearTimeout(id);
  }, [successMessage, navigate]);


  if (authLoading) {
    return (
      <Container className="py-5 text-center">
        <Spinner animation="border" />
      </Container>
    );
  }
  if (!user) return <Navigate to="/login" replace />;
  if (!user.is_admin) return <Navigate to="/" replace />;


  const onSubmit = async (data: FormData) => {
    setServerError(null);
    setSuccessMessage(null);
    redirectRef.current = null;

    const trimmed = data.name.trim();
    const nameChanged = trimmed !== originalNameRef.current;

    const logoFile: File | undefined =
      data.payment_logo instanceof FileList &&
      data.payment_logo.length > 0
        ? data.payment_logo[0]
        : undefined;

    if (!nameChanged && !logoFile) {
      setServerError("No changes were made");
      return;
    }

    if (logoFile) {
      if (!ALLOWED_TYPES.includes(logoFile.type)) {
        setError("payment_logo", {
          type: "manual",
          message: "Logo must be PNG, JPEG, WEBP, or SVG",
        });
        return;
      }
      if (logoFile.size > MAX_LOGO_BYTES) {
        setError("payment_logo", {
          type: "manual",
          message: "Logo must be under 2 MiB",
        });
        return;
      }
    }

    const formData = new FormData();
    if (nameChanged) {
      formData.append("name", trimmed);
    }
    if (logoFile) {
      formData.append("payment_logo", logoFile);
    }

    try {
      const res = await api.patch<UpdatePaymentMethodResponse>(
        `/payment-methods/${slug}`,
        formData
      );
      setSuccessMessage(res.data.message);
      redirectRef.current = {
        path: `/payment-methods/${res.data.payment_method.slug}`,
        delayMs: 1500,
      };
    } catch (err) {
      const msg = handleApiError<FormData>(err, {
        navigate,
        setError,
        validFields: ["name", "payment_logo"],
        redirectOnAuth: false,
        redirectOnForbidden: false,
      });
      if (msg) setServerError(msg);
    }
  };


  if (loading) {
    return (
      <Container className="py-5 text-center">
        <Spinner animation="border" />
      </Container>
    );
  }


  return (
    <Container className="py-5" style={{ maxWidth: 520 }}>
      <div className="bg-white p-4 rounded shadow-sm">
        <h1 className="h3 text-center mb-4">Update Payment Method</h1>

        {successMessage && (
          <Alert variant="success" className="text-center">
            {successMessage}
            <div className="small mt-1">Redirecting...</div>
          </Alert>
        )}
        {serverError && (
          <Alert variant="danger" className="text-center">
            {serverError}
          </Alert>
        )}

        <Form onSubmit={handleSubmit(onSubmit)} noValidate>
          <Form.Group className="mb-3" controlId="name">
            <Form.Label>Payment Method Name</Form.Label>
            <Form.Control
              type="text"
              isInvalid={!!errors.name}
              disabled={isSubmitting || successMessage !== null}
              {...register("name")}
            />
            <Form.Control.Feedback type="invalid">
              {errors.name?.message}
            </Form.Control.Feedback>
          </Form.Group>

          {currentLogoUrl && (
            <div className="mb-3">
              <div className="text-muted small mb-2">Current logo</div>
              <img
                src={currentLogoUrl}
                alt="Current payment logo"
                style={{
                  height: 48,
                  maxWidth: 160,
                  objectFit: "contain",
                  display: "block",
                }}
              />
            </div>
          )}

          <Form.Group className="mb-4" controlId="payment_logo">
            <Form.Label>
              {currentLogoUrl ? "Replace Logo" : "Logo"}{" "}
              <span className="text-muted small">(optional)</span>
            </Form.Label>
            <Form.Control
              type="file"
              accept="image/png,image/jpeg,image/webp,image/svg+xml"
              isInvalid={!!errors.payment_logo}
              disabled={isSubmitting || successMessage !== null}
              {...register("payment_logo")}
            />
            <Form.Control.Feedback type="invalid">
              {errors.payment_logo?.message as string | undefined}
            </Form.Control.Feedback>
            <Form.Text className="text-muted">
              Leave blank to keep the current logo.
            </Form.Text>
          </Form.Group>

          <Button
            type="submit"
            variant="primary"
            className="w-100"
            disabled={isSubmitting || successMessage !== null}
          >
            {isSubmitting ? (
              <>
                <Spinner
                  as="span"
                  animation="border"
                  size="sm"
                  role="status"
                  aria-hidden="true"
                  className="me-2"
                />
                Updating...
              </>
            ) : (
              "Update Payment Method"
            )}
          </Button>
        </Form>

        <p className="text-center mt-4 mb-0 small">
          <Link to={`/payment-methods/${slug}`}>
            ← Back to Payment Method
          </Link>
        </p>
      </div>
    </Container>
  );
};

export default UpdatePaymentMethod;




// src/lib/handleApiError.ts

const DEFAULT_ERROR_CODE_REDIRECTS: Partial<Record<ApiErrorCode, string>> = {
  account_suspended:   "/contact-admin",
  account_unverified:  "/register/verify",
  firm_suspended:      "/contact-admin",
  no_permission:       "/",
  wrong_permission:    "/",
  no_country_scope:    "/",   // ← kept for backwards compat; harmless if unused
  wrong_country_scope: "/",   // ← same
  not_admin:           "/",
};

















// src/pages/users/Register.tsx

import { useEffect, useRef, useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useNavigate, Link, Navigate } from "react-router-dom";
import axios from "axios";

import Form from "react-bootstrap/Form";
import Button from "react-bootstrap/Button";
import Container from "react-bootstrap/Container";
import Alert from "react-bootstrap/Alert";
import Spinner from "react-bootstrap/Spinner";

import api from "@/api/client";
import { useAuth } from "@/context/AuthContext";
import { handleApiError } from "@/lib/handleApiError";
import type { CreateUser, RegisterResponse } from "@/types";


// =============================================================
// VALIDATION
// =============================================================

const schema = z.object({
  surname: z
    .string()
    .trim()
    .min(2, "Surname must be at least 2 characters")
    .max(100, "Surname must not exceed 100 characters"),

  othernames: z
    .string()
    .trim()
    .min(2, "Other names must be at least 2 characters")
    .max(150, "Other names must not exceed 150 characters"),

  email: z
    .string()
    .trim()
    .email("Please enter a valid email address"),

  password: z
    .string()
    .min(6, "Password must be at least 6 characters"),
});

type FormData = z.infer<typeof schema>;


// =============================================================
// COMPONENT
// =============================================================

const Register = () => {
  const navigate = useNavigate();
  const { user, isLoading: authLoading } = useAuth();

  const [serverError, setServerError] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  const redirectRef = useRef<{
    email: string;
    reg_token: string;
    otp_attempts_used: number;
    otp_expires_in_seconds: number;
  } | null>(null);

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
    setError,
  } = useForm<FormData>({
    resolver: zodResolver(schema),
    defaultValues: {
      surname: "",
      othernames: "",
      email: "",
      password: "",
    },
  });


  // ===========================================================
  // Cancelable redirect
  // ===========================================================
  useEffect(() => {
    const p = redirectRef.current;
    if (!successMessage || !p) return;
    const id = window.setTimeout(() => {
      navigate("/register/verify", { replace: true, state: p });
    }, 1200);
    return () => window.clearTimeout(id);
  }, [successMessage, navigate]);


  // ===========================================================
  // Auth guards
  // ===========================================================
  if (authLoading) {
    return (
      <Container className="py-5 text-center">
        <Spinner animation="border" />
        <p className="text-muted mt-2">Loading...</p>
      </Container>
    );
  }
  if (user) return <Navigate to="/profile" replace />;


  // ===========================================================
  // Submit
  // ===========================================================
  const onSubmit = async (data: FormData) => {
    setServerError(null);
    setSuccessMessage(null);
    redirectRef.current = null;

    const payload: CreateUser = {
      surname: data.surname.trim(),
      othernames: data.othernames.trim(),
      email: data.email.trim().toLowerCase(),
      password: data.password,
    };

    try {
      const res = await api.post<RegisterResponse>("/auth/register", payload);

      redirectRef.current = {
        email: res.data.email,
        reg_token: res.data.reg_token,
        otp_attempts_used: res.data.otp_attempts_used,
        otp_expires_in_seconds: res.data.otp_expires_in_seconds,
      };

      setSuccessMessage(res.data.message);
    } catch (err) {
      const msg = handleApiError<FormData>(err, {
        navigate,
        setError,
        validFields: ["surname", "othernames", "email", "password"],
        redirectOnAuth: false,
        redirectOnForbidden: false,
      });
      if (msg) setServerError(msg);
    }
  };


  // ===========================================================
  // UI
  // ===========================================================
  return (
    <Container className="py-5" style={{ maxWidth: 480 }}>
      <div className="bg-white p-4 rounded shadow-sm">
        <h1 className="h3 text-center mb-4">Create Account</h1>

        {successMessage && (
          <Alert variant="success" className="text-center">
            {successMessage}
            <div className="small mt-1">Redirecting to verification...</div>
          </Alert>
        )}

        {serverError && (
          <Alert variant="danger" className="text-center">
            {serverError}
          </Alert>
        )}

        <Form onSubmit={handleSubmit(onSubmit)} noValidate>
          <Form.Group className="mb-3" controlId="surname">
            <Form.Label>Surname</Form.Label>
            <Form.Control
              type="text"
              autoComplete="family-name"
              isInvalid={!!errors.surname}
              disabled={isSubmitting || successMessage !== null}
              {...register("surname")}
            />
            <Form.Control.Feedback type="invalid">
              {errors.surname?.message}
            </Form.Control.Feedback>
          </Form.Group>

          <Form.Group className="mb-3" controlId="othernames">
            <Form.Label>Other Names</Form.Label>
            <Form.Control
              type="text"
              autoComplete="given-name"
              isInvalid={!!errors.othernames}
              disabled={isSubmitting || successMessage !== null}
              {...register("othernames")}
            />
            <Form.Control.Feedback type="invalid">
              {errors.othernames?.message}
            </Form.Control.Feedback>
          </Form.Group>

          <Form.Group className="mb-3" controlId="email">
            <Form.Label>Email</Form.Label>
            <Form.Control
              type="email"
              autoComplete="email"
              isInvalid={!!errors.email}
              disabled={isSubmitting || successMessage !== null}
              {...register("email")}
            />
            <Form.Control.Feedback type="invalid">
              {errors.email?.message}
            </Form.Control.Feedback>
          </Form.Group>

          <Form.Group className="mb-4" controlId="password">
            <Form.Label>Password</Form.Label>
            <Form.Control
              type="password"
              autoComplete="new-password"
              isInvalid={!!errors.password}
              disabled={isSubmitting || successMessage !== null}
              {...register("password")}
            />
            <Form.Control.Feedback type="invalid">
              {errors.password?.message}
            </Form.Control.Feedback>
          </Form.Group>

          <Button
            type="submit"
            variant="primary"
            className="w-100"
            disabled={isSubmitting || successMessage !== null}
          >
            {isSubmitting ? (
              <>
                <Spinner
                  as="span"
                  animation="border"
                  size="sm"
                  role="status"
                  aria-hidden="true"
                  className="me-2"
                />
                Creating...
              </>
            ) : (
              "Register"
            )}
          </Button>
        </Form>

        <p className="text-center mt-4 mb-0 small">
          Already have an account? <Link to="/login">Login</Link>
        </p>
      </div>
    </Container>
  );
};

export default Register;

// src/types/user/registration.ts

import type { ReadUser } from "./profile";
import type { OtpMeta } from "./otp";

export interface CreateUser {
  surname: string;
  othernames: string;
  email: string;
  password: string;
  // country_id: number  ← removed
}

export interface RegisterResponse extends OtpMeta {
  message: string;
  email: string;
  reg_token: string;
}

export interface VerifyRegistrationResponse {
  message: string;
  user: ReadUser;
}



// src/pages/ContactAdmin.tsx

import { useEffect, useRef, useState } from "react";
import { useLocation, useNavigate, Link } from "react-router-dom";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import axios from "axios";

import Form from "react-bootstrap/Form";
import Button from "react-bootstrap/Button";
import Container from "react-bootstrap/Container";
import Alert from "react-bootstrap/Alert";
import Spinner from "react-bootstrap/Spinner";

import api from "@/api/client";
import { handleApiError } from "@/lib/handleApiError";
import { humanizeSeconds } from "@/lib/time";
import type {
  ContactAdminMessage,
  ContactAdminResponse,
} from "@/types";


// =============================================================
// CONSTANTS
// =============================================================

const MESSAGE_MIN = 10;
const MESSAGE_MAX = 2000;


// =============================================================
// VALIDATION
// =============================================================

const schema = z.object({
  email: z
    .string()
    .trim()
    .email("Please enter a valid email address"),

  message: z
    .string()
    .trim()
    .min(MESSAGE_MIN, `Message must be at least ${MESSAGE_MIN} characters`)
    .max(MESSAGE_MAX, `Message must not exceed ${MESSAGE_MAX} characters`),
});

type FormData = z.infer<typeof schema>;


// =============================================================
// COMPONENT
// =============================================================

const ContactAdmin = () => {
  const navigate = useNavigate();
  const location = useLocation();

  const { email: prefillEmail } = (location.state || {}) as {
    email?: string;
  };

  const [serverError, setServerError] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);
  const [retryAfter, setRetryAfter] = useState<number | null>(null);

  const redirectRef = useRef<{ path: string; delayMs: number } | null>(null);

  const {
    register,
    handleSubmit,
    watch,
    formState: { errors, isSubmitting },
    setError,
  } = useForm<FormData>({
    resolver: zodResolver(schema),
    defaultValues: {
      email: prefillEmail ?? "",
      message: "",
    },
  });

  const messageValue = watch("message") ?? "";


  // ---------------------------------------------------------
  // Cancelable redirect
  // ---------------------------------------------------------
  useEffect(() => {
    const p = redirectRef.current;
    if (!p) return;
    const id = window.setTimeout(
      () => navigate(p.path, { replace: true }),
      p.delayMs
    );
    return () => window.clearTimeout(id);
  }, [successMessage, navigate]);


  // ---------------------------------------------------------
  // Retry-After countdown
  // ---------------------------------------------------------
  useEffect(() => {
    if (retryAfter === null || retryAfter <= 0) return;
    const id = window.setInterval(() => {
      setRetryAfter((s) => (s === null || s <= 1 ? null : s - 1));
    }, 1000);
    return () => window.clearInterval(id);
  }, [retryAfter]);


  // ---------------------------------------------------------
  // Submit
  // ---------------------------------------------------------
  const onSubmit = async (data: FormData) => {
    setServerError(null);
    setSuccessMessage(null);
    setRetryAfter(null);
    redirectRef.current = null;

    const payload: ContactAdminMessage = {
      email: data.email.trim().toLowerCase(),
      message: data.message.trim(),
    };

    try {
      const res = await api.post<ContactAdminResponse>(
        "/auth/contact-admin",
        payload
      );
      setSuccessMessage(res.data.message);
      redirectRef.current = { path: "/", delayMs: 4000 };
    } catch (err) {
      if (!axios.isAxiosError(err)) {
        setServerError("An unexpected error occurred.");
        return;
      }

      const status = err.response?.status;
      const detail = err.response?.data?.detail;

      // 422 — validation
      if (status === 422 && Array.isArray(detail)) {
        detail.forEach((item: unknown) => {
          if (typeof item !== "object" || item === null) return;
          const v = item as { loc?: unknown[]; msg?: string };
          const field = v.loc?.[v.loc.length - 1];
          if ((field === "email" || field === "message") && v.msg) {
            setError(field, { type: "server", message: v.msg });
          }
        });
        return;
      }

      // 429 — rate limit
      if (status === 429) {
        const header = err.response?.headers?.["retry-after"];
        const parsed = header ? parseInt(String(header), 10) : NaN;
        const seconds = Number.isFinite(parsed) && parsed > 0 ? parsed : null;

        setRetryAfter(seconds);
        setServerError(
          typeof detail === "string"
            ? detail
            : "Too many requests. Please try again later."
        );
        return;
      }

      // Catch-all — trust backend `detail`
      setServerError(
        typeof detail === "string"
          ? detail
          : `Request failed${status ? ` (${status})` : ""}. Please try again.`
      );
    }
  };


  const submitDisabled =
    isSubmitting ||
    successMessage !== null ||
    (retryAfter !== null && retryAfter > 0);


  // ---------------------------------------------------------
  // UI
  // ---------------------------------------------------------
  return (
    <Container className="py-5" style={{ maxWidth: 560 }}>
      <div className="bg-white p-4 rounded shadow-sm">
        <h1 className="h3 text-center mb-2">Contact Support</h1>

        <p className="text-muted text-center mb-4">
          If you're having trouble accessing your account, send us a
          message and our support team will review it.
        </p>

        {successMessage && (
          <Alert variant="success" className="text-center">
            {successMessage}
            <div className="small mt-1">Redirecting to home...</div>
          </Alert>
        )}

        {serverError && (
          <Alert variant="danger" className="text-center">
            {serverError}
            {retryAfter !== null && retryAfter > 0 && (
              <div className="small mt-1">
                You can try again in {humanizeSeconds(retryAfter)}.
              </div>
            )}
          </Alert>
        )}

        <Form onSubmit={handleSubmit(onSubmit)} noValidate>
          <Form.Group className="mb-3" controlId="email">
            <Form.Label>Your Email</Form.Label>
            <Form.Control
              type="email"
              autoComplete="email"
              placeholder="you@example.com"
              isInvalid={!!errors.email}
              disabled={isSubmitting || successMessage !== null}
              {...register("email")}
            />
            <Form.Control.Feedback type="invalid">
              {errors.email?.message}
            </Form.Control.Feedback>
          </Form.Group>

          <Form.Group className="mb-4" controlId="message">
            <Form.Label>Message</Form.Label>
            <Form.Control
              as="textarea"
              rows={6}
              placeholder="Describe your issue..."
              isInvalid={!!errors.message}
              disabled={isSubmitting || successMessage !== null}
              maxLength={MESSAGE_MAX}
              {...register("message")}
            />
            <Form.Control.Feedback type="invalid">
              {errors.message?.message}
            </Form.Control.Feedback>

            <div className="d-flex justify-content-between">
              <Form.Text className="text-muted">
                At least {MESSAGE_MIN} characters.
              </Form.Text>
              <Form.Text
                className={
                  messageValue.length >= MESSAGE_MAX
                    ? "text-danger"
                    : "text-muted"
                }
              >
                {messageValue.length} / {MESSAGE_MAX}
              </Form.Text>
            </div>
          </Form.Group>

          <Button
            type="submit"
            variant="primary"
            className="w-100"
            disabled={submitDisabled}
          >
            {isSubmitting ? (
              <>
                <Spinner
                  as="span"
                  animation="border"
                  size="sm"
                  role="status"
                  aria-hidden="true"
                  className="me-2"
                />
                Sending...
              </>
            ) : retryAfter !== null && retryAfter > 0 ? (
              `Try again in ${humanizeSeconds(retryAfter)}`
            ) : (
              "Send Message"
            )}
          </Button>
        </Form>

        <p className="text-center mt-4 mb-0 small">
          <Link to="/">Back to home</Link>
        </p>
      </div>
    </Container>
  );
};

export default ContactAdmin;


// src/types/user/contact.ts

export interface ContactAdminMessage {
  email: string;
  message: string;
}

export interface ContactAdminResponse {
  message: string;
}


// src/pages/Profile.tsx

import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import axios from "axios";

import Container from "react-bootstrap/Container";
import ListGroup from "react-bootstrap/ListGroup";
import Button from "react-bootstrap/Button";
import Alert from "react-bootstrap/Alert";
import Spinner from "react-bootstrap/Spinner";
import Image from "react-bootstrap/Image";

import api from "@/api/client";
import { useAuth } from "@/context/AuthContext";
import { handleApiError } from "@/lib/handleApiError";
import type { UserProfile } from "@/types";


// =============================================================
// HELPERS
// =============================================================

function resolveDisplayName(u: UserProfile): string {
  if (u.full_name && u.full_name.trim()) return u.full_name;
  const joined = [u.surname, u.othernames]
    .filter(Boolean)
    .join(" ")
    .trim();
  return joined || "—";
}


// =============================================================
// COMPONENT
// =============================================================

const Profile = () => {
  const navigate = useNavigate();
  const { logout, logoutMessage } = useAuth();

  const [user, setUser] = useState<UserProfile | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);


  // ===========================================================
  // Load profile
  // ===========================================================
  useEffect(() => {
    let mounted = true;

    const fetchProfile = async () => {
      try {
        setLoading(true);
        setError(null);

        const res = await api.get<UserProfile>("/auth/userprofile");
        if (mounted) setUser(res.data);
      } catch (err) {
        if (!mounted) return;

        const msg = handleApiError(err, {
          navigate,
          redirectOnAuth: false,
          redirectOnForbidden: false,
        });
        if (msg) setError(msg);
      } finally {
        if (mounted) setLoading(false);
      }
    };

    fetchProfile();

    return () => {
      mounted = false;
    };
  }, [navigate]);


  // ===========================================================
  // Delete account
  // ===========================================================
  const handleDeleteAccount = async () => {
    if (
      !window.confirm(
        "Are you sure you want to delete your account? This cannot be undone."
      )
    ) {
      return;
    }

    setDeleting(true);
    setError(null);

    try {
      const res = await api.delete<{ message?: string }>(
        "/auth/delete/account"
      );
      setMessage(res.data.message || "Account deleted successfully.");
      window.setTimeout(() => logout(), 1500);
    } catch (err) {
      const msg = handleApiError(err, {
        navigate,
        redirectOnAuth: false,
        redirectOnForbidden: false,
      });
      if (msg) setError(msg);
    } finally {
      setDeleting(false);
    }
  };


  // ===========================================================
  // Loading
  // ===========================================================
  if (loading) {
    return (
      <Container className="py-5 text-center">
        <Spinner animation="border" />
        <p className="mt-3 text-muted">Loading profile...</p>
      </Container>
    );
  }


  // ===========================================================
  // Error, no user
  // ===========================================================
  if (error && !user) {
    return (
      <Container className="py-5" style={{ maxWidth: 480 }}>
        <Alert variant="danger" className="text-center">
          {error}
        </Alert>
      </Container>
    );
  }

  if (!user) return null;

  const displayName = resolveDisplayName(user);
  const hasAvatar =
    typeof user.avatar === "string" && user.avatar.trim().length > 0;


  // ===========================================================
  // UI
  // ===========================================================
  return (
    <Container className="py-5" style={{ maxWidth: 640 }}>
      <div className="bg-white p-4 rounded shadow-sm">
        <h1 className="h3 mb-4">My Profile</h1>

        {logoutMessage && (
          <Alert variant="success" className="text-center">
            {logoutMessage}
          </Alert>
        )}

        {message && (
          <Alert variant="success" className="text-center">
            {message}
          </Alert>
        )}

        {error && (
          <Alert variant="danger" className="text-center">
            {error}
          </Alert>
        )}

        {/* =====================================================
            AVATAR
        ===================================================== */}
        <div className="d-flex justify-content-center mb-4">
          {hasAvatar ? (
            <Image
              src={user.avatar!}
              alt={`${displayName} avatar`}
              roundedCircle
              width={112}
              height={112}
              className="border shadow-sm"
              style={{ objectFit: "cover" }}
              onError={(e) => {
                (e.currentTarget as HTMLImageElement).style.display =
                  "none";
              }}
            />
          ) : (
            <div
              className="rounded-circle bg-light border d-flex align-items-center justify-content-center fw-bold text-secondary"
              style={{ width: 112, height: 112, fontSize: "2rem" }}
            >
              {displayName.charAt(0).toUpperCase()}
            </div>
          )}
        </div>

        {/* =====================================================
            DETAILS
        ===================================================== */}
        <ListGroup>
          <ListGroup.Item className="d-flex justify-content-between align-items-center">
            <div>
              <div className="text-muted small">Full Name</div>
              <div className="fw-medium">{displayName}</div>
            </div>
            <Link
              to="/change/names"
              className="btn btn-sm btn-outline-primary"
            >
              Edit
            </Link>
          </ListGroup.Item>

          <ListGroup.Item className="d-flex justify-content-between align-items-center">
            <div>
              <div className="text-muted small">Email</div>
              <div className="fw-medium">{user.email}</div>
            </div>
            <Link
              to="/email/approval"
              className="btn btn-sm btn-outline-primary"
            >
              Edit
            </Link>
          </ListGroup.Item>

          <ListGroup.Item className="d-flex justify-content-between align-items-center">
            <div>
              <div className="text-muted small">Password</div>
              <div className="fw-medium">••••••••</div>
            </div>
            <Link
              to="/change/password"
              className="btn btn-sm btn-outline-primary"
            >
              Edit
            </Link>
          </ListGroup.Item>

          {/* Removed: Country row */}

          <ListGroup.Item className="d-flex justify-content-between">
            <strong>Verified</strong>
            <span>{user.verified ? "Yes" : "No"}</span>
          </ListGroup.Item>

          <ListGroup.Item className="d-flex justify-content-between">
            <strong>Status</strong>
            <span>{user.disabled ? "Disabled" : "Active"}</span>
          </ListGroup.Item>

          {user.is_admin === true && (
            <ListGroup.Item className="d-flex justify-content-between">
              <strong>Admin</strong>
              <span>Yes</span>
            </ListGroup.Item>
          )}

          {user.name && (
            <ListGroup.Item className="d-flex justify-content-between">
              <strong>Group</strong>
              <span>{user.name}</span>
            </ListGroup.Item>
          )}

          {user.permission && (
            <ListGroup.Item className="d-flex justify-content-between">
              <strong>Permission</strong>
              <span>{user.permission}</span>
            </ListGroup.Item>
          )}

          <ListGroup.Item className="d-flex justify-content-between">
            <strong>Member Since</strong>
            <span>
              {new Date(user.created_at).toLocaleDateString("en-GB", {
                day: "numeric",
                month: "long",
                year: "numeric",
                timeZone: "UTC",
              })}
            </span>
          </ListGroup.Item>
        </ListGroup>

        {/* =====================================================
            ACTIONS
        ===================================================== */}
        <Button
          variant="outline-danger"
          className="w-100 mt-4"
          onClick={handleDeleteAccount}
          disabled={deleting}
        >
          {deleting ? "Deleting..." : "Delete my account"}
        </Button>

        <Button
          variant="danger"
          className="w-100 mt-3"
          onClick={logout}
        >
          Logout
        </Button>
      </div>
    </Container>
  );
};

export default Profile;



// src/types/user/profile.ts

export interface ReadUser {
  id: number;
  surname: string;
  othernames: string;
  email: string;
  is_admin: boolean;
  verified: boolean;
  disabled: boolean;
  date_verified?: string | null;
  created_at: string;
  permission?: string | null;
  full_name?: string;
  avatar?: string;
  // country?: string        ← removed
  // country_id?: number     ← removed
}

export interface UserProfile {
  id: number;
  email: string;
  surname: string;
  othernames: string;
  full_name?: string;
  avatar?: string;
  verified: boolean;
  disabled: boolean;
  date_verified?: string;
  created_at: string;
  updated_at: string;
  is_admin?: boolean;
  name?: string;
  permission?: string;
  // country?: string        ← removed
}


// src/components/Footer.tsx

import { Link } from "react-router-dom";

import AvailablePaymentMethods from "@/components/payments/AvailablePaymentMethods";
import { useSite } from "@/context/SiteContext";


const Footer = () => {
  const currentYear = new Date().getFullYear();
  const { settings } = useSite();

  const siteName = settings.sitename || "this site";

  return (
    <>
      {/* Payment methods */}
      <div className="container py-4">
        <AvailablePaymentMethods variant="grid" />
      </div>

      {/* Nav links */}
      <div className="text-center d-flex flex-row justify-content-center flex-wrap">
        <Link to="/contactus" className="p-2 flex-fill text-decoration-none">
          Contact Us
        </Link>
        <Link to="/aboutus" className="p-2 flex-fill text-decoration-none">
          About Us
        </Link>
        <Link
          to="/privacypolicy"
          className="p-2 flex-fill text-decoration-none"
        >
          Privacy Policy
        </Link>
        <Link
          to="/terms/conditions"
          className="p-2 flex-fill text-decoration-none"
        >
          Terms &amp; Conditions
        </Link>
      </div>

      {/* Copyright */}
      <p className="mt-4 text-dark text-center">
        &copy; {currentYear}. All rights reserved.{" "}
        <Link to="/" className="text-decoration-none text-info">
          {siteName}
        </Link>
      </p>
    </>
  );
};


export default Footer;















// src/components/payments/AvailablePaymentMethods.tsx

import Spinner from "react-bootstrap/Spinner";
import Alert from "react-bootstrap/Alert";
import Row from "react-bootstrap/Row";
import Col from "react-bootstrap/Col";

import { useAvailablePaymentMethods } from "@/hooks/useAvailablePaymentMethods";
import type { PaymentMethodRead } from "@/types";


interface AvailablePaymentMethodsProps {
  /** Optional heading above the grid. */
  title?: string;

  /**
   * "grid" (default) — responsive column layout.
   * "inline" — compact flex-wrap row.
   */
  variant?: "grid" | "inline";
}


const AvailablePaymentMethods = ({
  title = "We accept",
  variant = "grid",
}: AvailablePaymentMethodsProps) => {
  const { data, loading, error } = useAvailablePaymentMethods();

  if (loading) {
    return (
      <div className="text-center py-3">
        <Spinner animation="border" size="sm" role="status" />
        <span className="text-muted small ms-2">Loading...</span>
      </div>
    );
  }

  if (error) {
    return (
      <Alert variant="danger" className="py-2 small mb-0">
        {error}
      </Alert>
    );
  }

  if (!data || data.methods.length === 0) {
    return (
      <p className="text-muted small text-center mb-0">
        No available payment option in our system now
      </p>
    );
  }

  if (variant === "inline") {
    return (
      <div>
        {title && (
          <div className="text-muted small text-center mb-2">{title}</div>
        )}
        <div className="d-flex flex-wrap justify-content-center align-items-center gap-3">
          {data.methods.map((method) => (
            <PaymentLogo key={method.id} method={method} />
          ))}
        </div>
      </div>
    );
  }

  return (
    <div>
      {title && (
        <div className="text-muted small text-center mb-3">{title}</div>
      )}
      <Row
        xs={3}
        sm={4}
        md={5}
        lg={6}
        xl={8}
        className="g-3 justify-content-center align-items-center"
      >
        {data.methods.map((method) => (
          <Col
            key={method.id}
            className="d-flex justify-content-center align-items-center"
          >
            <PaymentLogo method={method} />
          </Col>
        ))}
      </Row>
    </div>
  );
};


/**
 * A single logo tile. Falls back to the payment method name when
 * no logo URL is present, so the widget still reads well.
 */
function PaymentLogo({ method }: { method: PaymentMethodRead }) {
  if (!method.payment_logo_url) {
    return (
      <span
        className="badge bg-light text-dark border fw-normal text-truncate"
        style={{
          maxWidth: "100%",
          fontSize: "0.7rem",
          padding: "4px 8px",
        }}
        title={method.name}
      >
        {method.name}
      </span>
    );
  }

  return (
    <img
      src={method.payment_logo_url}
      alt={method.name}
      title={method.name}
      style={{
        height: 28,
        width: "auto",
        maxWidth: "100%",
        objectFit: "contain",
        display: "block",
      }}
      loading="lazy"
    />
  );
}


export default AvailablePaymentMethods;



// src/types/payment/read.ts

export interface PaymentMethodRead {
  id: number;
  name: string;
  slug?: string | null;
  payment_logo_url?: string | null;
  created_at: string;
  updated_at: string;
}

export interface AvailablePaymentMethodsRead {
  total: number;
  methods: PaymentMethodRead[];
}


// src/types/payment/create.ts

import type { PaymentMethodRead } from "./read";

export interface PaymentMethodCreate {
  name: string;
}

export interface CreatePaymentMethodResponse {
  message: string;
  payment_method: PaymentMethodRead;
}

// src/types/payment/update.ts

import type { PaymentMethodRead } from "./read";

export interface PaymentMethodUpdate {
  name?: string;
}

export interface UpdatePaymentMethodResponse {
  message: string;
  payment_method: PaymentMethodRead;
}

export * from "./read";
export * from "./create";
export * from "./update";



// src/types/user/contact.ts

export interface ContactAdminMessage {
  email: string;
  message: string;
}

export interface ContactAdminResponse {
  message: string;
}




// src/hooks/useAvailablePaymentMethods.ts

import { useEffect, useState } from "react";
import axios from "axios";

import api from "@/api/client";
import type { AvailablePaymentMethodsRead } from "@/types";

interface UseAvailablePaymentMethodsResult {
  data: AvailablePaymentMethodsRead | null;
  loading: boolean;
  error: string | null;
}

/**
 * Fetch every payment method accepted on the platform.
 *
 * Public endpoint — used by the Footer / landing page widget.
 */
export function useAvailablePaymentMethods(): UseAvailablePaymentMethodsResult {
  const [data, setData] = useState<AvailablePaymentMethodsRead | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    let mounted = true;

    api
      .get<AvailablePaymentMethodsRead>("/payment-methods/available", {
        signal: controller.signal,
      })
      .then((res) => {
        if (mounted) setData(res.data);
      })
      .catch((err) => {
        if (axios.isCancel(err)) return;
        if (!mounted) return;

        if (!axios.isAxiosError(err)) {
          setError("An unexpected error occurred.");
          return;
        }

        const status = err.response?.status;
        const detail = err.response?.data?.detail;

        setError(
          typeof detail === "string"
            ? detail
            : `Request failed (${status ?? "unknown"}).`
        );
      })
      .finally(() => {
        if (mounted && !controller.signal.aborted) setLoading(false);
      });

    return () => {
      mounted = false;
      controller.abort();
    };
  }, []);

  return { data, loading, error };




          








# api/payments/schemas.py

from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field, field_validator


def normalize_payment_option(value: str) -> str:
    """Uppercase + collapse whitespace. Single source of truth for names."""
    import re
    if not value or not value.strip():
        raise ValueError("Payment method name cannot be empty")
    return re.sub(r"\s+", " ", value.strip()).upper()


# =============================================================================
# REQUEST
# =============================================================================

class PaymentMethodCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=2, max_length=100)
    # country_id: int  ← removed

    @field_validator("name", mode="before")
    @classmethod
    def validate_name(cls, v: str) -> str:
        return normalize_payment_option(v)


class PaymentMethodUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None

    @field_validator("name", mode="before")
    @classmethod
    def validate_name(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return normalize_payment_option(v)


# =============================================================================
# READ
# =============================================================================

class PaymentMethodRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    slug: str | None = None
    payment_logo_url: str | None = None
    created_at: datetime
    updated_at: datetime
    # country_id: int  ← removed


class AvailablePaymentMethodsRead(BaseModel):
    """All payment methods. Single-country mode — flat list."""
    total: int
    methods: list[PaymentMethodRead]


# =============================================================================
# RESPONSE ENVELOPES
# =============================================================================

class CreatePaymentMethodResponse(BaseModel):
    message: str
    payment_method: PaymentMethodRead


class UpdatePaymentMethodResponse(BaseModel):
    message: str
    payment_method: PaymentMethodRead


class MessageResponse(BaseModel):
    message: str




# api/payments/logics.py
"""Payment methods business logic — single-country mode."""

import logging

from fastapi import HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from api.core.file_storage import (
    PAYMENT_LOGO,
    PAYMENT_LOGO_MAX_SIZE,
    delete_from_r2,
    get_public_url,
    handle_file_update,
)
from api.core.slug import generate_slug
from api.payments.models import PaymentMethods
from api.payments.schemas import (
    AvailablePaymentMethodsRead,
    PaymentMethodCreate,
    PaymentMethodRead,
    PaymentMethodUpdate,
)
from api.users.schemas import ReadUser

logger = logging.getLogger(__name__)


# =============================================================================
# HELPERS
# =============================================================================

def to_payment_method_read(method: PaymentMethods) -> PaymentMethodRead:
    """Build a PaymentMethodRead with the logo URL resolved from its key."""
    read = PaymentMethodRead.model_validate(method)
    return read.model_copy(
        update={
            "payment_logo_url": (
                get_public_url(method.payment_logo_key)
                if method.payment_logo_key
                else None
            ),
        }
    )


def payment_method_id_of(method: PaymentMethods) -> int:
    if method.id is None:
        logger.error("Payment method row has no id — data integrity issue")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load payment method. Please try again.",
        )
    return method.id


async def get_payment_by_slug(
    db: AsyncSession,
    slug: str,
) -> PaymentMethods | None:
    """Fetch a payment method by slug."""
    try:
        normalized = slug.strip().lower()
        result = await db.execute(
            select(PaymentMethods).where(
                col(PaymentMethods.slug) == normalized
            )
        )
        return result.scalars().first()
    except Exception:
        logger.exception(
            "Failed to fetch payment method slug=%s", slug
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load payment method. Please try again.",
        )


# =============================================================================
# CREATE — ADMIN ONLY
# =============================================================================

async def create_payment_method(
    data: PaymentMethodCreate,
    payment_logo: UploadFile | None,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """
    Create a payment method.

    Admin only — enforced by the route's `admin: AdminUser` dependency.

    `payment_logo` is optional. Uploaded to R2 before the DB insert;
    cleaned up if the insert fails.
    """

    # ------------------------------------------------------------------
    # Uniqueness — name is globally unique now
    # ------------------------------------------------------------------
    try:
        existing = (
            await db.execute(
                select(PaymentMethods).where(
                    col(PaymentMethods.name) == data.name,
                )
            )
        ).scalars().first()
    except Exception:
        logger.exception(
            "Failed to check existing payment method name=%s", data.name
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create payment method. Please try again.",
        )

    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Payment method '{data.name}' already exists",
        )

    # ------------------------------------------------------------------
    # Upload logo (before DB write so we can clean up on failure)
    # ------------------------------------------------------------------
    new_logo_key: str | None = None
    if payment_logo is not None:
        new_logo_key = await handle_file_update(
            file=payment_logo,
            current_key=None,
            prefix=PAYMENT_LOGO,
            max_size=PAYMENT_LOGO_MAX_SIZE,
        )

    # ------------------------------------------------------------------
    # Slug — derived from name only
    # ------------------------------------------------------------------
    method_slug = generate_slug(data.name)

    method = PaymentMethods(
        name=data.name,
        slug=method_slug,
        payment_logo_key=new_logo_key,
    )

    # ------------------------------------------------------------------
    # Persist
    # ------------------------------------------------------------------
    try:
        db.add(method)
        await db.commit()
        await db.refresh(method)
    except Exception:
        await db.rollback()
        logger.exception(
            "Failed to create payment method name=%s", data.name
        )

        if new_logo_key:
            try:
                delete_from_r2(new_logo_key)
            except Exception:
                logger.exception(
                    "Failed to clean up orphaned logo key=%s",
                    new_logo_key,
                )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create payment method. Please try again.",
        )

    logger.info(
        "Payment method '%s' (slug=%s) created by admin user_id=%s",
        method.name,
        method.slug,
        current_user.id,
    )

    return {
        "message": f"Payment method '{method.name}' created successfully",
        "payment_method": to_payment_method_read(method),
    }


# =============================================================================
# READ — all (admin only)
# =============================================================================

async def read_all_payment_methods(
    db: AsyncSession,
    skip: int = 0,
    limit: int = 100,
) -> AvailablePaymentMethodsRead:
    """
    Return every payment method.

    Admin-only — enforced by the route's `admin: AdminUser` dependency.
    """
    try:
        total: int = (
            await db.execute(
                select(func.count()).select_from(PaymentMethods)
            )
        ).scalar() or 0

        result = await db.execute(
            select(PaymentMethods)
            .order_by(col(PaymentMethods.name))
            .offset(skip)
            .limit(limit)
        )
        methods = result.scalars().all()
    except Exception:
        logger.exception("Failed to list payment methods")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load payment methods. Please try again.",
        )

    return AvailablePaymentMethodsRead(
        total=total,
        methods=[to_payment_method_read(m) for m in methods],
    )


# =============================================================================
# READ — available (public, deduplicated — for the Footer widget)
# =============================================================================

async def read_available_payment_methods(
    db: AsyncSession,
) -> AvailablePaymentMethodsRead:
    """
    Return one instance of each unique payment method.

    Public — no authentication. Used by the public "We accept"
    widget on the Footer and landing page.

    In single-country mode, "deduplicated" is a no-op since names
    are globally unique. Kept for API stability if multi-country
    returns later.
    """
    try:
        result = await db.execute(
            select(PaymentMethods).order_by(col(PaymentMethods.name))
        )
        methods = result.scalars().all()
    except Exception:
        logger.exception("Failed to load available payment methods")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load payment methods. Please try again.",
        )

    return AvailablePaymentMethodsRead(
        total=len(methods),
        methods=[to_payment_method_read(m) for m in methods],
    )


# =============================================================================
# READ — single (admin only)
# =============================================================================

async def read_single_payment_method(
    slug: str,
    db: AsyncSession,
) -> PaymentMethodRead:
    """Get a single payment method by slug. Admin-only via route."""
    method = await get_payment_by_slug(db, slug)
    if not method:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Payment method '{slug}' not found",
        )
    return to_payment_method_read(method)


# =============================================================================
# UPDATE — ADMIN ONLY
# =============================================================================

async def update_payment_method(
    slug: str,
    data: PaymentMethodUpdate,
    payment_logo: UploadFile | None,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """
    Update a payment method by slug.

    Admin only — enforced by the route's `admin: AdminUser` dependency.

    `name` and `payment_logo` are both optional. Passing a new logo
    replaces the old one; omitting it leaves the existing logo intact.
    """
    method = await get_payment_by_slug(db, slug)
    if not method:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Payment method '{slug}' not found",
        )

    if data.name is None and payment_logo is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one field must be provided for update",
        )

    updated_fields: list[str] = []

    # ------------------------------------------------------------------
    # Name change
    # ------------------------------------------------------------------
    if data.name is not None and data.name != method.name:
        try:
            existing = (
                await db.execute(
                    select(PaymentMethods).where(
                        col(PaymentMethods.name) == data.name,
                        col(PaymentMethods.id)
                        != payment_method_id_of(method),
                    )
                )
            ).scalars().first()
        except Exception:
            logger.exception(
                "Failed to check duplicate payment method name=%s",
                data.name,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to update payment method. Please try again.",
            )

        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Payment method '{data.name}' already exists",
            )

        old_slug = method.slug
        method.name = data.name
        method.slug = generate_slug(data.name)
        updated_fields.append("name")

        logger.info(
            "Payment method name changed: slug '%s' -> '%s'",
            old_slug,
            method.slug,
        )

    # ------------------------------------------------------------------
    # Logo replacement
    # ------------------------------------------------------------------
    old_logo_key = method.payment_logo_key
    new_logo_key: str | None = None

    if payment_logo is not None:
        try:
            new_logo_key = await handle_file_update(
                file=payment_logo,
                current_key=old_logo_key,
                prefix=PAYMENT_LOGO,
                max_size=PAYMENT_LOGO_MAX_SIZE,
            )
        except HTTPException:
            raise
        except Exception:
            logger.exception(
                "Failed to upload new logo for payment method slug=%s",
                slug,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to update payment method. Please try again.",
            )

        method.payment_logo_key = new_logo_key
        updated_fields.append("payment_logo")

    if not updated_fields:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "No changes detected - all supplied values are "
                "identical to the current ones"
            ),
        )

    # ------------------------------------------------------------------
    # Persist
    # ------------------------------------------------------------------
    try:
        db.add(method)
        await db.commit()
        await db.refresh(method)
    except Exception:
        await db.rollback()
        logger.exception("Failed to update payment method slug=%s", slug)

        if new_logo_key and new_logo_key != old_logo_key:
            try:
                delete_from_r2(new_logo_key)
            except Exception:
                logger.exception(
                    "Failed to clean up new logo key=%s", new_logo_key
                )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update payment method. Please try again.",
        )

    if (
        new_logo_key
        and old_logo_key
        and new_logo_key != old_logo_key
    ):
        try:
            delete_from_r2(old_logo_key)
        except Exception:
            logger.exception(
                "Failed to delete old payment logo key=%s", old_logo_key
            )

    logger.info(
        "Payment method id=%s updated by admin user_id=%s (fields=%s)",
        payment_method_id_of(method),
        current_user.id,
        ",".join(updated_fields),
    )

    return {
        "message": f"Payment method '{method.name}' updated successfully",
        "payment_method": to_payment_method_read(method),
    }


# =============================================================================
# DELETE — ADMIN ONLY
# =============================================================================

async def delete_payment_method(
    slug: str,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """Delete a payment method by slug. Admin-only via route."""
    method = await get_payment_by_slug(db, slug)
    if not method:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Payment method '{slug}' not found",
        )

    method_name = method.name

    try:
        await db.delete(method)
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("Failed to delete payment method slug=%s", slug)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete payment method. Please try again.",
        )

    logger.info(
        "Payment method '%s' deleted by admin user_id=%s",
        method_name,
        current_user.id,
    )

    return {
        "message": f"Payment method '{method_name}' deleted successfully"
    }



# api/payments/routes.py

from fastapi import APIRouter, File, Form, UploadFile, status

from api.core.database import DBDep
from api.payments.logics import (
    create_payment_method,
    delete_payment_method,
    read_all_payment_methods,
    read_available_payment_methods,
    read_single_payment_method,
    update_payment_method,
)
from api.payments.schemas import (
    AvailablePaymentMethodsRead,
    CreatePaymentMethodResponse,
    MessageResponse,
    PaymentMethodCreate,
    PaymentMethodRead,
    PaymentMethodUpdate,
    UpdatePaymentMethodResponse,
)
from api.users.deps import AdminUser

router = APIRouter(prefix="/payment-methods", tags=["Payment Methods"])


# =============================================================================
# PUBLIC
#
# Declared BEFORE /{slug} so `/available` doesn't get swallowed by
# the dynamic path.
# =============================================================================

@router.get(
    "/available",
    response_model=AvailablePaymentMethodsRead,
    status_code=status.HTTP_200_OK,
    summary="List payment methods accepted (public)",
)
async def list_available_payment_methods(db: DBDep):
    """
    Public — no authentication.

    Used by the Footer widget and landing page to show the payment
    methods the platform supports.
    """
    return await read_available_payment_methods(db=db)


# =============================================================================
# ADMIN READS
# =============================================================================

@router.get(
    "",
    response_model=AvailablePaymentMethodsRead,
    status_code=status.HTTP_200_OK,
    summary="List all payment methods (admin only)",
)
async def list_all_payment_methods(
    db: DBDep,
    admin: AdminUser,
    skip: int = 0,
    limit: int = 100,
):
    return await read_all_payment_methods(
        db=db, skip=skip, limit=limit
    )


@router.get(
    "/{slug}",
    response_model=PaymentMethodRead,
    status_code=status.HTTP_200_OK,
    summary="Get a single payment method (admin only)",
)
async def get_payment_method(
    slug: str,
    db: DBDep,
    admin: AdminUser,
):
    return await read_single_payment_method(slug=slug, db=db)


# =============================================================================
# ADMIN WRITES — multipart (logo upload)
# =============================================================================

@router.post(
    "",
    response_model=CreatePaymentMethodResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a payment method (admin only)",
)
async def create_method(
    db: DBDep,
    admin: AdminUser,
    name: str = Form(...),
    payment_logo: UploadFile | None = File(None),
):
    data = PaymentMethodCreate(name=name)

    return await create_payment_method(
        data=data,
        payment_logo=payment_logo,
        db=db,
        current_user=admin,
    )


@router.patch(
    "/{slug}",
    response_model=UpdatePaymentMethodResponse,
    status_code=status.HTTP_200_OK,
    summary="Update a payment method (admin only)",
)
async def patch_payment_method(
    slug: str,
    db: DBDep,
    admin: AdminUser,
    name: str | None = Form(None),
    payment_logo: UploadFile | None = File(None),
):
    data = PaymentMethodUpdate(name=name)

    return await update_payment_method(
        slug=slug,
        data=data,
        payment_logo=payment_logo,
        db=db,
        current_user=admin,
    )


@router.delete(
    "/{slug}",
    response_model=MessageResponse,
    status_code=status.HTTP_200_OK,
    summary="Delete a payment method (admin only)",
)
async def remove_payment_method(
    slug: str,
    db: DBDep,
    admin: AdminUser,
):
    return await delete_payment_method(
        slug=slug, db=db, current_user=admin
)







async def handle_disabled_account(
    data: ContactAdminMessage,
    db: AsyncSession,
    redis: Redis,
    mailer: FastMail,
    background_tasks: BackgroundTasks,
) -> dict:
    """
    Handle contact form from disabled users.

    Enumeration-safe: returns the same generic response whether the
    account exists or is disabled.

    Support email is `settings.mail_username` — single-country mode.
    When offices carry a support address, this can pick the first
    office's email instead.
    """
    user = await get_user_by_email(db, data.email)

    # Generic response — don't reveal account status
    if not user or not user.disabled:
        return {
            "message": (
                "If your account exists, your message has been sent "
                "to our support team."
            )
        }

    user_id = get_user_id(user)

    # ------------------------------------------------------------------
    # Rate limit — max N per window
    # ------------------------------------------------------------------
    rate_key = f"contact_admin_rate:{user_id}"
    count = await redis.incr(rate_key)

    if count == 1:
        await redis.expire(
            rate_key,
            int(
                timedelta(
                    minutes=settings.contact_rate_window
                ).total_seconds()
            ),
        )

    if count > settings.contact_rate_limit:
        ttl = await redis.ttl(rate_key)
        default_window = int(
            timedelta(
                minutes=settings.contact_rate_window
            ).total_seconds()
        )
        retry_after = (
            ttl if isinstance(ttl, int) and ttl > 0 else default_window
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please try again later.",
            headers={"Retry-After": str(retry_after)},
        )

    # ------------------------------------------------------------------
    # Capture primitives — user ORM is not safe to pass to the task
    # ------------------------------------------------------------------
    full_names = user.full_names

    # Support email — single-country mode uses settings.mail_username.
    # (Offices carry their own contact info but for a global
    # "contact admin" flow, mail_username is the right recipient.)
    support_email = settings.mail_username

    background_tasks.add_task(
        send_support_message,
        user_full_names=full_names,
        support_email=support_email,
        user_email=data.email,
        message=data.message,
        mailer=mailer,
    )

    logger.info(
        "Disabled user %s (id=%s) contacted support → %s",
        data.email,
        user_id,
        support_email,
    )

    return {
        "message": (
            "Your message has been sent to our support team. "
            "We will review your account and get back to you."
        )
    }






