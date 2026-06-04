import { json } from "../constants.js";

export class ValidationError extends Error {
  constructor(message) {
    super(message);
    this.name = "ValidationError";
    this.code = "VALIDATION_ERROR";
  }
}

export function validateString(value, name, maxLength = 10000) {
  if (typeof value !== "string") {
    throw new ValidationError(`${name} must be a string`);
  }
  if (value.length > maxLength) {
    throw new ValidationError(`${name} exceeds maximum length of ${maxLength}`);
  }
  return value;
}

export function validateArray(value, name) {
  if (!Array.isArray(value)) {
    throw new ValidationError(`${name} must be an array`);
  }
  return value;
}

export function sanitizeScope(scope) {
  if (!scope) return "personal";
  const allowed = ["personal", "company", "global"];
  return allowed.includes(scope) ? scope : "personal";
}

export function sanitizeOwnerId(ownerId) {
  if (!ownerId || typeof ownerId !== "string") return "anonymous";
  return ownerId.replace(/[^a-zA-Z0-9_-]/g, "").slice(0, 64);
}

export function truncateId(id) {
  if (id.length <= 64) return id;
  const hash = Array.from(id).reduce((h, c) => ((h << 5) - h + c.charCodeAt(0)) | 0, 0);
  return id.slice(0, 50) + "_" + Math.abs(hash).toString(36);
}
