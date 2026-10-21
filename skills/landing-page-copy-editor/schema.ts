import { defineSchema, defineTable } from "convex/server";
import { v } from "convex/values";
import { typePlatformTables } from "./typeSchema";

export default defineSchema({
  ...typePlatformTables,
  copyEdits: defineTable({
    page: v.string(),
    key: v.string(),
    text: v.string(),
    updatedAt: v.number(),
    updatedBy: v.union(v.string(), v.null()),
  }).index("by_page_key", ["page", "key"]),
});
