import { v } from "convex/values";
import { mutation, query } from "./typeFunctions";

const PAGE_PATTERN = /^[a-z0-9-]{1,64}$/;
const KEY_PATTERN = /^c\d{3,4}$/;
const MAX_TEXT = 4000;

function assertPage(page: string) {
  if (!PAGE_PATTERN.test(page)) throw new Error("Invalid page");
}
function assertKey(key: string) {
  if (!KEY_PATTERN.test(key)) throw new Error("Invalid copy key");
}

export const status = query({
  args: {},
  returns: v.object({ ready: v.boolean() }),
  handler: async () => ({ ready: true }),
});

const editValidator = v.object({
  key: v.string(),
  text: v.string(),
  updatedAt: v.number(),
  updatedBy: v.union(v.string(), v.null()),
});

export const listEdits = query({
  args: { page: v.string() },
  returns: v.array(editValidator),
  handler: async (ctx, { page }) => {
    assertPage(page);
    const rows = await ctx.db
      .query("copyEdits")
      .withIndex("by_page_key", (q) => q.eq("page", page))
      .take(2000);
    return rows.map((r) => ({
      key: r.key,
      text: r.text,
      updatedAt: r.updatedAt,
      updatedBy: r.updatedBy,
    }));
  },
});

export const setEdit = mutation({
  args: { page: v.string(), key: v.string(), text: v.string(), now: v.number() },
  returns: v.null(),
  handler: async (ctx, { page, key, text, now }) => {
    assertPage(page);
    assertKey(key);
    if (text.length > MAX_TEXT) throw new Error("Text is too long");
    if (!Number.isFinite(now) || now < 0 || now > 8.64e15) throw new Error("Invalid time");
    const identity = await ctx.auth.getUserIdentity();
    const updatedBy =
      identity && identity.viewer_type === "member" && typeof identity.name === "string"
        ? identity.name
        : null;
    const existing = await ctx.db
      .query("copyEdits")
      .withIndex("by_page_key", (q) => q.eq("page", page).eq("key", key))
      .unique();
    if (existing) {
      await ctx.db.patch(existing._id, { text, updatedAt: now, updatedBy });
    } else {
      await ctx.db.insert("copyEdits", { page, key, text, updatedAt: now, updatedBy });
    }
    return null;
  },
});

export const revertEdit = mutation({
  args: { page: v.string(), key: v.string() },
  returns: v.null(),
  handler: async (ctx, { page, key }) => {
    assertPage(page);
    assertKey(key);
    const existing = await ctx.db
      .query("copyEdits")
      .withIndex("by_page_key", (q) => q.eq("page", page).eq("key", key))
      .unique();
    if (existing) await ctx.db.delete(existing._id);
    return null;
  },
});
