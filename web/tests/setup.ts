import "@testing-library/jest-dom/vitest";

// jsdom has no native modal-dialog implementation. Browser checks cover focus trapping.
HTMLDialogElement.prototype.showModal = function () {
  this.setAttribute("open", "");
};
HTMLDialogElement.prototype.close = function () {
  this.removeAttribute("open");
};
