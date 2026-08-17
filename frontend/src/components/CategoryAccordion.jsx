import React, { useState } from "react";

/**
 * Expandable category sections with the closed-set tile images.
 * `groups` from groupItemsByCategory / groupNamesByCategory.
 * `renderItem(item)` draws one row; shopping list may pass strings.
 */
export default function CategoryAccordion({
  groups,
  renderItem,
  initiallyOpen = null
}) {
  const [openId, setOpenId] = useState(initiallyOpen);

  if (!groups?.length) {
    return null;
  }

  return (
    <div className="category-accordion">
      {groups.map((group) => {
        const open = openId === group.id;
        const count = group.items.length;
        return (
          <div
            key={group.id}
            className={open ? "category-panel category-panel-open" : "category-panel"}
          >
            <button
              type="button"
              className="category-header"
              aria-expanded={open}
              onClick={() => setOpenId(open ? null : group.id)}
            >
              <img
                className="category-image"
                src={group.image}
                alt=""
                width="56"
                height="56"
              />
              <div className="category-copy">
                <strong>{group.label}</strong>
                <span className="hint">
                  {count} {count === 1 ? "item" : "items"}
                </span>
              </div>
              <span className="category-chevron" aria-hidden="true">
                {open ? "−" : "+"}
              </span>
            </button>
            {open ? (
              <ul className="category-items item-list">
                {group.items.map((item, index) => (
                  <li key={item.id || `${group.id}-${index}`} className="item-card">
                    {renderItem(item)}
                  </li>
                ))}
              </ul>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}
