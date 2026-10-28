import { parseThemePreference } from "./themePreference";
import { useAppearance } from "./ThemeProvider";

export function AppearanceControl() {
  const { preference, storageAvailable, setPreference } = useAppearance();

  return (
    <div className="appearance-control">
      <label htmlFor="workspace-appearance-preference">外观</label>
      <select
        id="workspace-appearance-preference"
        onChange={(event) => setPreference(parseThemePreference(event.currentTarget.value))}
        value={preference}
      >
        <option value="light">明亮</option>
        <option value="dark">深色</option>
        <option value="auto">跟随系统</option>
      </select>
      {!storageAvailable && (
        <p className="appearance-control-status" role="status">
          浏览器无法保存外观偏好；当前页面仍会应用选择。
        </p>
      )}
    </div>
  );
}
