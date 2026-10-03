-- Two stored procedures, comments and a plain query in one MySQL dump.
DELIMITER $$

CREATE PROCEDURE compute_total(IN x INT)
BEGIN
  -- semicolons inside the body are ordinary statement text
  SELECT 'a;b;c' AS literal;
  IF x > 0 THEN
    SELECT x * 2 AS doubled;
  END IF;
END$$

CREATE FUNCTION forty_two() RETURNS INT
BEGIN
  /* a block comment mentioning DELIMITER $$ and ; */
  RETURN 42;
END $$

DELIMITER //

CREATE TRIGGER before_insert BEFORE INSERT ON t
FOR EACH ROW
BEGIN
  SET NEW.created_at = NOW();
END//

DELIMITER ;

-- A regular query after switching back to the semicolon.
SELECT id, name FROM t WHERE name = 'DELIMITER $$';
